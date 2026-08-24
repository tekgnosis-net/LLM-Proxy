import re
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import JSONResponse
from app.auth import login_required
from app.settings import get_settings
from app.config_db import ConfigStore
from app.config_render import effective, render_config, redact_rendered
from app.config_engine import apply_config, pending_status, ApplyError, _make_resolve_key
from app.credentials_store import fernet_from_secret
from app.config_store import ConfigError, validate_config, write_config_atomic
from app.reloader import Reloader
from app.models_client import ModelsClient
from app.model_reconcile import build_desired, diff_models, reconcile_models
from app.model_content import content_diff
from app.mcp_reconcile import build_desired as build_mcp_desired, mcp_content_diff, reconcile_mcp, MCP_TEAM_ID
from app.config_integrity import group_names, router_orphans, key_orphans, trim_router_setting, trim_key_field, mga_names_from, key_mcp_orphans, mcp_server_names
from app.reachability import collision_audit, key_over_reach, SEMANTICS_VERSION
from app.keys_client import KeysClient
from app.mcp_client import McpClient
import yaml as _yaml

router = APIRouter(prefix="/api")

def make_config_store() -> ConfigStore:
    s = get_settings()
    if not s.database_url: raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return ConfigStore(s.database_url)

def _fernet():
    s = get_settings(); return fernet_from_secret(s.credentials_key or s.session_secret)

def make_reloader() -> Reloader:
    s = get_settings()
    return Reloader(s.socket_proxy_url, s.litellm_base_url, s.litellm_master_key,
                    s.litellm_container, mode=s.reload_mode, timeout_s=s.reload_timeout_s)

def make_models_client() -> ModelsClient:
    s = get_settings()
    return ModelsClient(s.litellm_base_url, s.litellm_master_key)

def make_mcp_client() -> McpClient:
    s = get_settings()
    return McpClient(s.litellm_base_url, s.litellm_master_key)

def make_keys_client() -> KeysClient:
    s = get_settings()
    return KeysClient(s.litellm_base_url, s.litellm_master_key)

async def _guard_empty_master(store, body: dict | None) -> None:
    """Spec §7: refuse mass-delete when the master is empty but LiteLLM serves models."""
    if body and body.get("force") is True:
        return
    eff = effective(await store.applied(), await store.staged())
    master_models = [i for i in eff if i["kind"] == "model" and i.get("flag") != "deleted"]
    if master_models:
        return
    try:
        live = await make_models_client().list_models()
    except Exception:
        return                      # can't see live: don't block on a probe failure
    if live:
        raise HTTPException(status_code=409, detail=(
            f"master config is empty but LiteLLM serves {len(live)} models — refusing to "
            f"delete them; restore from Backup & Restore, or pass force:true to wipe deliberately"))

def _redact_item(it: dict) -> dict:
    if it["kind"] == "credential":
        d = it["data"] or {}
        return {**it, "data": {"provider": d.get("provider"), "api_key": "***"}}
    if it["kind"] == "mcp_server":
        d = dict(it["data"] or {})
        if d.get("auth_value_encrypted"):
            d["auth_value_encrypted"] = "***"
        return {**it, "data": d}
    return it

@router.get("/config/export", dependencies=[Depends(login_required)])
async def export_config():
    store = make_config_store()
    items = await store.applied()   # [{kind,name,data}] — credentials carry value_encrypted, never plaintext
    payload = {"version": 1, "items": items}
    return JSONResponse(payload, headers={"Content-Disposition": "attachment; filename=ui_config.json"})

@router.get("/config/state", dependencies=[Depends(login_required)])
async def config_state():
    store = make_config_store()
    try:
        eff = effective(await store.applied(), await store.staged())
        n = await store.staged_count()
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"config state error: {e}")
    return {"items": [_redact_item(i) for i in eff], "pending": n > 0, "count": n,
            "store_model_in_db": get_settings().store_model_in_db}

async def _credential_data(name: str, data: dict, store) -> dict:
    """Build a credential's stored data. A provided api_key is Fernet-encrypted; a
    BLANK api_key reuses the existing credential's value_encrypted (edit without
    re-typing the secret). Blank with no existing credential is rejected."""
    data = data or {}
    provider = data.get("provider")
    api_key = data.get("api_key")
    if api_key:
        ve = _fernet().encrypt(api_key.encode()).decode()
    else:
        eff = effective(await store.applied(), await store.staged())
        existing = next((i for i in eff if i["kind"] == "credential" and i["name"] == name
                         and i.get("flag") != "deleted"), None)
        ve = (existing.get("data") or {}).get("value_encrypted") if existing else None
        if not ve:
            raise HTTPException(status_code=422, detail="credential api_key required (no existing key to keep)")
    return {"provider": provider, "value_encrypted": ve}

_MCP_TRANSPORTS = {"http", "sse"}
_MCP_AUTH_TYPES = {"api_key", "bearer_token", "basic"}
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

def _mcp_cost_info(data: dict) -> dict:
    ci = ((data.get("mcp_info") or {}).get("mcp_server_cost_info") or {})
    out = {}
    dc = ci.get("default_cost_per_query")
    if dc is not None:
        if not isinstance(dc, (int, float)) or isinstance(dc, bool) or dc < 0:
            raise HTTPException(status_code=422, detail="default_cost_per_query must be a number >= 0")
        out["default_cost_per_query"] = float(dc)
    tools = ci.get("tool_name_to_cost_per_query") or {}
    if tools:
        clean = {}
        for t, v in tools.items():
            if not isinstance(t, str) or not t.strip() or not isinstance(v, (int, float)) \
                    or isinstance(v, bool) or v < 0:
                raise HTTPException(status_code=422, detail="tool costs must map tool name -> number >= 0")
            clean[t.strip()] = float(v)
        out["tool_name_to_cost_per_query"] = clean
    return {"mcp_server_cost_info": out} if out else {}

async def _mcp_server_data(name: str, data: dict, store) -> dict:
    """Normalize + validate an mcp_server item. A provided auth_value is Fernet-encrypted;
    BLANK auth_value with auth_type set reuses the existing ciphertext (edit without
    re-typing). config.yaml never sees these items — hot apply only."""
    data = dict(data or {})
    server_name = (data.get("server_name") or "").strip()
    if not _MCP_NAME_RE.match(server_name):
        raise HTTPException(status_code=422, detail="server_name required: letters, digits or _ only (LiteLLM rejects '-' — it's the tool-name separator)")
    url = (data.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="url required (http:// or https://)")
    transport = data.get("transport")
    if transport not in _MCP_TRANSPORTS:
        raise HTTPException(status_code=422, detail="transport must be http or sse")
    auth_type = (data.get("auth_type") or None)
    if auth_type is not None and auth_type not in _MCP_AUTH_TYPES:
        raise HTTPException(status_code=422, detail="auth_type must be api_key, bearer_token, basic or empty")
    eff = effective(await store.applied(), await store.staged())
    for it in eff:
        if (it["kind"] == "mcp_server" and it["name"] != name and it.get("flag") != "deleted"
                and (it.get("data") or {}).get("server_name") == server_name):
            raise HTTPException(status_code=422, detail=f"server_name {server_name!r} already in use")
    out = {
        "server_name": server_name,
        "description": (data.get("description") or "").strip(),
        "transport": transport,
        "url": url,
        "auth_type": auth_type,
        "static_headers": {str(k).strip(): str(v) for k, v in (data.get("static_headers") or {}).items()
                           if str(k).strip()},
        "extra_headers": [str(h).strip() for h in (data.get("extra_headers") or []) if str(h).strip()],
        "allowed_tools": [str(t).strip() for t in (data.get("allowed_tools") or []) if str(t).strip()],
        "allow_all_keys": bool(data.get("allow_all_keys")),
        "mcp_info": _mcp_cost_info(data),
    }
    if auth_type:
        auth_value = data.get("auth_value")
        if auth_value:
            out["auth_value_encrypted"] = _fernet().encrypt(auth_value.encode()).decode()
        else:
            existing = next((i for i in eff if i["kind"] == "mcp_server" and i["name"] == name
                             and i.get("flag") != "deleted"), None)
            ve = (existing.get("data") or {}).get("auth_value_encrypted") if existing else None
            if not ve:
                raise HTTPException(status_code=422, detail="auth_value required (no existing secret to keep)")
            out["auth_value_encrypted"] = ve
    return out

@router.put("/config/item", dependencies=[Depends(login_required)])
async def stage_item(body: dict = Body(...)):
    kind, name, data = body.get("kind"), body.get("name"), body.get("data")
    if not kind or not name: raise HTTPException(status_code=422, detail="kind and name required")
    if kind == "credential":
        data = await _credential_data(name, data, make_config_store())
    if kind == "mcp_server":
        data = await _mcp_server_data(name, data, make_config_store())
    try:
        await make_config_store().stage(kind, name, data)
        return await pending_status(make_config_store())
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"stage error: {e}")

@router.delete("/config/item/{kind}/{name}", dependencies=[Depends(login_required)])
async def delete_item(kind: str, name: str):
    try:
        await make_config_store().stage(kind, name, {}, deleted=True)
        return await pending_status(make_config_store())
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"stage error: {e}")

@router.post("/apply", dependencies=[Depends(login_required)])
async def apply(body: dict | None = Body(None)):
    s = get_settings(); f = _fernet()
    if s.store_model_in_db:
        await _guard_empty_master(make_config_store(), body)
    try:
        result = await apply_config(
            s.config_path, make_config_store(), make_reloader(),
            decrypt=lambda b: f.decrypt(b.encode()).decode(),
            models_client=make_models_client() if s.store_model_in_db else None,
            mcp_client=make_mcp_client() if s.store_model_in_db else None,
            hybrid=s.store_model_in_db,
        )
    except ApplyError as e:
        msg = str(e)
        code = 422 if ("invalid" in msg or "integrity" in msg) else 500
        raise HTTPException(status_code=code, detail=msg)
    try:
        from app.routes.backup_routes import make_backup_engine
        make_backup_engine().write_snapshot(await make_config_store().applied())
    except Exception:
        import logging; logging.getLogger("uvicorn.error").warning(
            "apply snapshot failed", exc_info=True)
    return result

@router.post("/discard", dependencies=[Depends(login_required)])
async def discard(kind: str | None = None, name: str | None = None):
    await make_config_store().clear_staged(kind, name)
    return await pending_status(make_config_store())

@router.get("/config/rendered", dependencies=[Depends(login_required)])
async def rendered():
    s = get_settings()
    store = make_config_store(); f = _fernet()
    eff = effective(await store.applied(), await store.staged())
    cfg = render_config(eff, decrypt=lambda b: f.decrypt(b.encode()).decode(), hybrid=s.store_model_in_db)
    return {"config": redact_rendered(cfg)}

@router.get("/config/passthrough", dependencies=[Depends(login_required)])
async def get_passthrough():
    store = make_config_store()
    eff = {(i["kind"], i["name"]): i for i in effective(await store.applied(), await store.staged())}
    it = eff.get(("passthrough", "_"))
    data = (it["data"] if it and it.get("flag") != "deleted" else {}) or {}
    return {"data": data, "yaml": _yaml.safe_dump(data, sort_keys=False) if data else ""}

@router.put("/config/passthrough", dependencies=[Depends(login_required)])
async def put_passthrough(body: dict = Body(...)):
    raw = body.get("yaml", "")
    try:
        data = _yaml.safe_load(raw) or {}
        if not isinstance(data, dict): raise ValueError("passthrough must be a YAML mapping")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid passthrough YAML: {e}")
    await make_config_store().stage("passthrough", "_", data)
    return await pending_status(make_config_store())

@router.post("/config/prepare-hot-apply", dependencies=[Depends(login_required)])
async def prepare_hot_apply():
    s = get_settings(); f = _fernet()
    store = make_config_store()
    # Make ui_config (the master) agree with the STORE_MODEL_IN_DB=true env, so the
    # rendered config + export are reproducible — not just the runtime env. Staged
    # here; folded by the post-recreate Apply.
    await store.stage('general_setting', 'store_model_in_db', True)
    eff = effective(await store.applied(), await store.staged())
    cfg = render_config(eff, decrypt=lambda b: f.decrypt(b.encode()).decode(), hybrid=True)
    try:
        validate_config(cfg)
        write_config_atomic(s.config_path, _yaml.safe_dump(cfg, sort_keys=False))
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=f"invalid config: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    try:
        await make_reloader().reload_and_verify([])   # comes up with zero models
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"proxy did not restart cleanly: {e}")
    return {"prepared": True,
            "next": "config.yaml now has no models. Set STORE_MODEL_IN_DB=true in .env, run "
                    "`docker compose up -d` to recreate the stack, then click Apply to fill the model DB."}

@router.post("/config/resync", dependencies=[Depends(login_required)])
async def config_resync(body: dict | None = Body(None)):
    s = get_settings()
    if not s.store_model_in_db:
        raise HTTPException(status_code=422, detail="resync requires hybrid mode (STORE_MODEL_IN_DB=true)")
    f = _fernet(); store = make_config_store()
    await _guard_empty_master(store, body)
    applied = await store.applied()
    model_items = [it for it in applied if it["kind"] == "model"]
    resolve_key = _make_resolve_key(applied, lambda b: f.decrypt(b.encode()).decode())
    client = make_models_client()
    live = await client.list_models()
    model_report = await reconcile_models(model_items, live, client,
                                          changed_item_names=set(), creds_changed=set(),
                                          resolve_key=resolve_key, converge_content=True)
    dec = lambda b: f.decrypt(b.encode()).decode()
    mcp_items = [it for it in applied if it["kind"] == "mcp_server"]
    mcp_client = make_mcp_client()
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
    return {**model_report, "mcp": mcp_report}

@router.get("/config/drift", dependencies=[Depends(login_required)])
async def config_drift():
    s = get_settings()
    if not s.store_model_in_db:
        return {"hybrid": False, "in_sync": True, "missing_in_litellm": [], "extra_in_litellm": []}
    store = make_config_store()
    applied_items = await store.applied()
    model_items = [it for it in applied_items if it["kind"] == "model"]
    desired, _, _ = build_desired(model_items, resolve_key=None)   # presence only — no key needed
    try:
        live = await make_models_client().list_models()
    except Exception as e:
        return {"error": "query_failed", "detail": str(e)}
    plan = diff_models(desired, live, set(), set())
    live_by_id = {(m.get("model_info") or {}).get("id"): m
                  for m in live if (m.get("model_info") or {}).get("id")}
    missing = [{"id": e["model_info"]["id"], "model_name": e.get("model_name")} for e in plan["to_add"]]
    extra = [{"id": i, "model_name": (live_by_id.get(i) or {}).get("model_name")} for i in plan["to_delete"]]
    content = []
    for mid in sorted(set(desired) & set(live_by_id)):
        fields = content_diff(desired[mid].get("model_info") or {},
                              (live_by_id[mid].get("model_info") or {}))
        if fields:
            content.append({"id": mid, "model_name": desired[mid].get("model_name"), "fields": fields})
    mcp_items = [it for it in applied_items if it["kind"] == "mcp_server"]
    try:
        mcp_live = await make_mcp_client().list_servers()
    except Exception as e:
        mcp_out = {"error": "query_failed", "detail": str(e)}
    else:
        desired_mcp, _ = build_mcp_desired(mcp_items, None)
        live_by_id_mcp = {s.get("server_id"): s for s in mcp_live if s.get("server_id")}
        mcp_out = {
            "missing_in_litellm": [{"id": i, "server_name": desired_mcp[i].get("server_name")}
                                   for i in sorted(set(desired_mcp) - set(live_by_id_mcp))],
            "extra_in_litellm": [{"id": i, "server_name": (live_by_id_mcp[i] or {}).get("server_name")}
                                 for i in sorted(set(live_by_id_mcp) - set(desired_mcp))],
            "content_drifted": [],
        }
        for sid in sorted(set(desired_mcp) & set(live_by_id_mcp)):
            fields = mcp_content_diff(desired_mcp[sid], live_by_id_mcp[sid])
            if fields:
                mcp_out["content_drifted"].append(
                    {"id": sid, "server_name": desired_mcp[sid].get("server_name"), "fields": fields})
    return {"hybrid": True, "in_sync": not missing and not extra and not content,
            "missing_in_litellm": missing, "extra_in_litellm": extra, "content_drifted": content,
            "mcp": mcp_out}

@router.get("/config/integrity", dependencies=[Depends(login_required)])
async def config_integrity():
    store = make_config_store()
    eff = effective(await store.applied(), await store.staged())
    router_items = [i for i in eff if i["kind"] == "router_setting" and i.get("flag") != "deleted"]
    groups = group_names([i for i in eff if i["kind"] == "model"], mga_names_from(router_items))
    r_orphans = router_orphans(router_items, groups)
    try:
        keys = await make_keys_client().list_keys()
    except Exception as e:
        return {"error": "query_failed", "detail": str(e), "router_orphans": r_orphans, "key_orphans": [], "key_mcp_orphans": []}
    k_orphans = key_orphans(keys, groups)
    mcp_valid = mcp_server_names([i for i in eff if i["kind"] == "mcp_server"])
    k_mcp = key_mcp_orphans(keys, mcp_valid)
    return {"in_sync": not r_orphans and not k_orphans and not k_mcp,
            "router_orphans": r_orphans, "key_orphans": k_orphans, "key_mcp_orphans": k_mcp}

@router.post("/config/integrity/fix", dependencies=[Depends(login_required)])
async def config_integrity_fix(body: dict = Body(...)):
    orphan = body.get("orphan") or {}
    dry = bool(body.get("dry_run"))
    scope = orphan.get("scope")
    target = orphan.get("target") or {}
    if scope == "router":
        store = make_config_store()
        eff = {(i["kind"], i["name"]): i for i in effective(await store.applied(), await store.staged())}
        it = eff.get(("router_setting", target.get("setting")))
        before = (it or {}).get("data")
        after = trim_router_setting(before, target)
        if dry:
            return {"before": before, "after": after,
                    "effect": "stages a config change (needs Apply + restart)"}
        if after in (None, [], {}):
            await store.stage("router_setting", target["setting"], {}, deleted=True)
        else:
            await store.stage("router_setting", target["setting"], after)
        return {"staged": True, "needs_apply": True}
    if scope == "key":
        keys = {k.get("token"): k for k in await make_keys_client().list_keys()}
        k = keys.get(target.get("token"))
        if k is None:
            raise HTTPException(status_code=409, detail="key not found (already changed?); re-scan")
        field = target["field"]
        if field == "mcp_servers":
            op = k.get("object_permission") if isinstance(k.get("object_permission"), dict) else {}
            before = op.get("mcp_servers")
            after = trim_key_field(before, target)
            # Empty grants on a ui-mcp team key FAIL OPEN to the whole team scope
            # (live-proven) — trimming the last grant must also detach the key.
            # Foreign-team keys are left alone (documented v1 limitation).
            detach = (not after) and k.get("team_id") == MCP_TEAM_ID
            if dry:
                effect = "applies immediately (hot)"
                if detach:
                    effect += "; last grant — also detaches the key from the ui-mcp team"
                return {"before": before, "after": after, "effect": effect}
            payload = {"key": target["token"], "object_permission": {"mcp_servers": after}}
            if detach:
                payload["team_id"] = None
            await make_keys_client().update_key(payload)
            return {"applied": True, "needs_apply": False}
        before = k.get(field)
        after = trim_key_field(before, target)
        if dry:
            return {"before": before, "after": after, "effect": "applies immediately (hot)"}
        await make_keys_client().update_key({"key": target["token"], field: after})
        return {"applied": True, "needs_apply": False}
    raise HTTPException(status_code=422, detail="orphan.scope must be 'router' or 'key'")

@router.get("/config/reachability", dependencies=[Depends(login_required)])
async def config_reachability():
    store = make_config_store()
    eff = effective(await store.applied(), await store.staged())
    model_items = [i for i in eff if i["kind"] == "model"]
    router_items = [i for i in eff if i["kind"] == "router_setting" and i.get("flag") != "deleted"]
    collisions = collision_audit(model_items, router_items)
    groups = group_names(model_items, mga_names_from(router_items))
    mga_item = next((i for i in router_items if i["name"] == "model_group_alias"), None)
    mga = mga_item["data"] if (mga_item and isinstance(mga_item.get("data"), dict)) else {}
    try:
        keys = await make_keys_client().list_keys()
    except Exception as e:
        return {"error": "query_failed", "detail": str(e), "semantics_version": SEMANTICS_VERSION,
                "collisions": collisions, "key_over_reach": []}
    return {"semantics_version": SEMANTICS_VERSION, "collisions": collisions,
            "key_over_reach": key_over_reach(keys, collisions, groups, mga)}
