from __future__ import annotations

import yaml

from app.config_render import effective, render_config
from app.config_store import validate_config, ConfigError, write_config_atomic
from app.config_integrity import group_names, router_orphans, mga_names_from
from app.model_reconcile import reconcile_models
from app.mcp_reconcile import reconcile_mcp
from app.reloader import ReloadError

_RESTART_KINDS = {"router_setting", "litellm_setting", "general_setting", "passthrough"}


class ApplyError(RuntimeError):
    pass


def _expected_models(cfg: dict) -> list[str]:
    """Extract model names from a rendered config dict."""
    return [m.get("model_name") for m in (cfg.get("model_list") or [])]


def _make_resolve_key(eff, decrypt):
    creds = {it["name"]: it for it in eff
             if it["kind"] == "credential" and it.get("flag") != "deleted"}
    def resolve(name):
        it = creds.get(name)
        if not it:
            return None
        ve = (it["data"] or {}).get("value_encrypted")
        return decrypt(ve) if ve else None
    return resolve


async def apply_config(config_path, store, reloader, *, decrypt, models_client=None,
                       mcp_client=None, hybrid=False) -> dict:
    """Commit-at-write apply flow.

    Non-hybrid (default, hybrid=False):
      Pre-commit (failure raises ApplyError, nothing folded):
        1. Render effective config from applied + staged.
        2. validate_config — reject invalid config before touching disk.
        3. Atomic write + read-back + re-parse.
      Commit:
        4. store.fold() — staged→applied, clear staged. This is the commit boundary.
      Post-commit (no rollback, reported via servant status):
        5. reloader.reload_and_verify(expected_models).
           Success → servant="healthy"; ReloadError → servant="unhealthy", applied still True.

    Hybrid (hybrid=True):
      Pre-commit: if settings changed, render settings-only config (hybrid=True) +
        validate + write; otherwise skip file write.
      Commit: store.fold().
      Post-commit: reconcile_models (reported, not rolled back);
        restart only if settings changed.
      Returns {"applied": True, "hybrid": True, "models": <report>,
               "restart": "healthy"|"unhealthy"|"skipped", "detail"?: str}.
    """
    applied = await store.applied()
    staged = await store.staged()
    eff = effective(applied, staged)

    # Referential-integrity gate (pre-commit, both modes): a fallback / model_group_alias
    # that names a group which does not exist would render a dangling reference. Block
    # before any write/fold so nothing is committed.
    _model_items = [it for it in eff if it["kind"] == "model"]
    _router_items = [it for it in eff if it["kind"] == "router_setting" and it.get("flag") != "deleted"]
    _groups = group_names(_model_items, mga_names_from(_router_items))
    _orphans = router_orphans(_router_items, _groups)
    if _orphans:
        detail = "; ".join(f'{o["location"]} references missing {o["reference"]!r}' for o in _orphans)
        raise ApplyError(f"integrity: {detail}; fix in the Integrity panel")

    if not hybrid and any(s["kind"] == "mcp_server" for s in staged):
        raise ApplyError("invalid config, not applied: mcp_server items require hybrid mode "
                         "(STORE_MODEL_IN_DB=true) — MCP servers hot-apply and are never rendered")

    if not hybrid:
        # ---- existing non-hybrid flow (unchanged) ----
        cfg = render_config(eff, decrypt)

        # Validate (pre-commit — raise ApplyError on failure, nothing written/folded)
        try:
            validate_config(cfg)
        except ConfigError as e:
            raise ApplyError(f"invalid config, not applied: {e}") from e

        # Atomic write + read-back + re-parse (pre-commit — raise ApplyError on failure).
        #    write_config_atomic now owns the readback: it reads the temp file and
        #    yaml.safe_loads it BEFORE os.replace, so the live file is untouched on
        #    any readback failure.
        text = yaml.safe_dump(cfg, sort_keys=False)
        try:
            write_config_atomic(config_path, text)
        except Exception as e:
            raise ApplyError(f"write/readback failed, not applied: {e}") from e

        # COMMIT: fold staged into applied, clear staged.
        #    The file is live and verified-good at this point.  A fold() failure
        #    means the file is correct but the DB staging table wasn't cleared —
        #    staged is intact (fold is transactional), so a re-Apply will re-fold.
        try:
            await store.fold()
        except Exception as e:
            raise ApplyError(
                f"config written to file but staging not cleared (DB error): {e}; "
                "re-Apply to finalize"
            ) from e

        # Restart + verify (post-commit — reported, NOT rolled back)
        expected = _expected_models(cfg)
        try:
            await reloader.reload_and_verify(expected)
            return {"applied": True, "servant": "healthy", "models": expected}
        except ReloadError as e:
            return {"applied": True, "servant": "unhealthy", "detail": str(e), "models": expected}

    # ---- hybrid flow ----
    if models_client is None:
        raise ApplyError("hybrid=True requires a models_client")
    settings_changed = any(s["kind"] in _RESTART_KINDS for s in staged)
    creds_changed = {s["name"] for s in staged if s["kind"] == "credential"}
    changed_ids = {s["name"] for s in staged if s["kind"] == "model" and s.get("flag") in ("new", "changed")}

    if settings_changed:                     # pre-commit: settings-only config
        cfg = render_config(eff, decrypt, hybrid=True)
        try:
            validate_config(cfg)
        except ConfigError as e:
            raise ApplyError(f"invalid config, not applied: {e}") from e
        try:
            write_config_atomic(config_path, yaml.safe_dump(cfg, sort_keys=False))
        except Exception as e:
            raise ApplyError(f"write/readback failed, not applied: {e}") from e

    try:                                     # commit
        await store.fold()
    except Exception as e:
        raise ApplyError(
            f"config written to file but staging not cleared (DB error): {e}; "
            "re-Apply to finalize"
        ) from e

    # post-commit (reported, not rolled back)
    resolve_key = _make_resolve_key(eff, decrypt)
    desired_items = [it for it in eff if it["kind"] == "model" and it.get("flag") != "deleted"]
    live = await models_client.list_models()
    model_report = await reconcile_models(desired_items, live, models_client,
                                          changed_item_names=changed_ids, creds_changed=creds_changed,
                                          resolve_key=resolve_key)
    out = {"applied": True, "hybrid": True, "models": model_report}
    if mcp_client is not None:
        # post-commit, reported never rolled back — mirrors the model reconcile.
        # Runs on EVERY hybrid apply (declarative self-healing, same as models):
        # servers created directly in LiteLLM get removed; the master is authoritative.
        mcp_changed = {s["name"] for s in staged
                       if s["kind"] == "mcp_server" and s.get("flag") in ("new", "changed")}
        mcp_items = [it for it in eff if it["kind"] == "mcp_server" and it.get("flag") != "deleted"]
        try:
            mcp_live = await mcp_client.list_servers()
            out["mcp"] = await reconcile_mcp(mcp_items, mcp_live, mcp_client, mcp_changed, decrypt)
        except Exception as e:
            out["mcp"] = {"added": 0, "updated": 0, "deleted": 0,
                          "failed": [{"id": "*", "op": "list", "error": str(e)}]}
    if settings_changed:
        expected = [(it["data"] or {}).get("model_name", it["name"]) for it in desired_items]
        try:
            await reloader.reload_and_verify(expected)
            out["restart"] = "healthy"
        except ReloadError as e:
            out["restart"] = "unhealthy"
            out["detail"] = str(e)
    else:
        out["restart"] = "skipped"
    return out


async def pending_status(store) -> dict:
    """Return {pending: bool, count: int} from store's staged count."""
    n = await store.staged_count()
    return {"pending": n > 0, "count": n}
