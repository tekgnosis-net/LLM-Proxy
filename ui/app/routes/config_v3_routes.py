from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import JSONResponse
from app.auth import login_required
from app.settings import get_settings
from app.config_db import ConfigStore
from app.config_render import effective, render_config, redact_rendered
from app.config_engine import apply_config, pending_status, ApplyError
from app.credentials_store import fernet_from_secret
from app.config_store import ConfigError, validate_config, write_config_atomic
from app.reloader import Reloader
from app.models_client import ModelsClient
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

def _redact_item(it: dict) -> dict:
    if it["kind"] == "credential":
        d = it["data"] or {}
        return {**it, "data": {"provider": d.get("provider"), "api_key": "***"}}
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
    return {"items": [_redact_item(i) for i in eff], "pending": n > 0, "count": n}

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

@router.put("/config/item", dependencies=[Depends(login_required)])
async def stage_item(body: dict = Body(...)):
    kind, name, data = body.get("kind"), body.get("name"), body.get("data")
    if not kind or not name: raise HTTPException(status_code=422, detail="kind and name required")
    if kind == "credential":
        data = await _credential_data(name, data, make_config_store())
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
async def apply():
    s = get_settings(); f = _fernet()
    try:
        return await apply_config(
            s.config_path, make_config_store(), make_reloader(),
            decrypt=lambda b: f.decrypt(b.encode()).decode(),
            models_client=make_models_client() if s.store_model_in_db else None,
            hybrid=s.store_model_in_db,
        )
    except ApplyError as e:
        code = 422 if "invalid" in str(e) else 500
        raise HTTPException(status_code=code, detail=str(e))

@router.post("/discard", dependencies=[Depends(login_required)])
async def discard(kind: str | None = None, name: str | None = None):
    await make_config_store().clear_staged(kind, name)
    return await pending_status(make_config_store())

@router.get("/config/rendered", dependencies=[Depends(login_required)])
async def rendered():
    store = make_config_store(); f = _fernet()
    eff = effective(await store.applied(), await store.staged())
    cfg = render_config(eff, decrypt=lambda b: f.decrypt(b.encode()).decode())
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
