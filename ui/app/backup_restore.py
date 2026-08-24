"""Restore flows: rollback (hot), full recovery (cold), logs merge (spec §6)."""
from __future__ import annotations
import json
from typing import Any

import yaml

from app.config_render import render_config
from app.config_engine import _make_resolve_key
from app.config_store import write_config_atomic
from app.model_reconcile import reconcile_models
from app.mcp_reconcile import build_desired as build_mcp_desired, mcp_content_diff, reconcile_mcp

RESTART_KINDS = {"router_setting", "litellm_setting", "general_setting", "passthrough"}


def parse_export(text: str) -> list[dict]:
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not valid JSON: {e}")
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ValueError("expected {version, items: [...]} export shape")
    for it in items:
        if not isinstance(it, dict) or "kind" not in it or "name" not in it or "data" not in it:
            raise ValueError("every item needs kind, name, data")
    return items


def rollback_preview(current: list[dict], new: list[dict]) -> dict:
    cur = {(i["kind"], i["name"]): i["data"] for i in current}
    nxt = {(i["kind"], i["name"]): i["data"] for i in new}
    added = sorted(set(nxt) - set(cur))
    removed = sorted(set(cur) - set(nxt))
    changed = sorted(k for k in set(cur) & set(nxt) if cur[k] != nxt[k])
    touched = added + removed + changed
    return {"added": [{"kind": k, "name": n} for k, n in added],
            "removed": [{"kind": k, "name": n} for k, n in removed],
            "changed": [{"kind": k, "name": n} for k, n in changed],
            "restart_kinds_changed": any(k in RESTART_KINDS for k, _ in touched)}


def check_decryptable(items: list[dict], fernet) -> list[str]:
    bad = []
    for it in items:
        d = it.get("data") or {}
        for field in ("value_encrypted", "auth_value_encrypted"):
            v = d.get(field)
            if v:
                try:
                    fernet.decrypt(v.encode())
                except Exception:
                    bad.append(f'{it["kind"]}/{it["name"]}')
                break
    return bad


async def rollback_config(items: list[dict], *, config_store, models_client, mcp_client,
                          reloader, config_path: str, fernet) -> dict:
    """Replace the master with `items`, then converge exactly like resync + a
    settings-diff-driven restart (spec §6.1). Caller has already run pre-checks."""
    dec = lambda b: fernet.decrypt(b.encode()).decode()
    await config_store.replace_applied(items)
    applied = await config_store.applied()

    model_items = [it for it in applied if it["kind"] == "model"]
    resolve_key = _make_resolve_key(applied, dec)
    live = await models_client.list_models()
    model_report = await reconcile_models(model_items, live, models_client,
                                          changed_item_names={it["name"] for it in model_items},
                                          creds_changed=set(), resolve_key=resolve_key,
                                          converge_content=True)
    mcp_items = [it for it in applied if it["kind"] == "mcp_server"]
    try:
        mcp_live = await mcp_client.list_servers()
        desired_mcp, _ = build_mcp_desired(mcp_items, None)
        live_by_id = {s.get("server_id"): s for s in mcp_live if s.get("server_id")}
        drifted = {sid for sid in (set(desired_mcp) & set(live_by_id))
                   if mcp_content_diff(desired_mcp[sid], live_by_id[sid])}
        mcp_report = await reconcile_mcp(mcp_items, mcp_live, mcp_client, drifted, dec)
    except Exception as e:
        mcp_report = {"added": 0, "updated": 0, "deleted": 0,
                      "failed": [{"id": "*", "op": "list", "error": str(e)}]}

    out: dict[str, Any] = {"applied": True, "models": model_report, "mcp": mcp_report}
    rendered = render_config(applied, dec, hybrid=True)
    try:
        on_disk = yaml.safe_load(open(config_path)) or {}
    except OSError:
        on_disk = None
    if rendered != on_disk:
        write_config_atomic(config_path, yaml.safe_dump(rendered, sort_keys=False))
        expected = [(it["data"] or {}).get("model_name", it["name"]) for it in model_items]
        try:
            await reloader.reload_and_verify(expected)
            out["restart"] = "healthy"
        except Exception as e:
            out["restart"] = "unhealthy"; out["detail"] = str(e)
    else:
        out["restart"] = "skipped"
    return out
