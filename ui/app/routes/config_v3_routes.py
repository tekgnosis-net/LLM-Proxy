from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.settings import get_settings
from app.config_db import ConfigStore
from app.config_render import effective, render_config, redact_rendered
from app.config_engine import apply_config, pending_status, ApplyError
from app.credentials_store import fernet_from_secret
from app.config_store import ConfigError
from app.reloader import Reloader

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

def _redact_item(it: dict) -> dict:
    if it["kind"] == "credential":
        d = it["data"] or {}
        return {**it, "data": {"provider": d.get("provider"), "api_key": "***"}}
    return it

@router.get("/config/state", dependencies=[Depends(login_required)])
async def config_state():
    store = make_config_store()
    try:
        eff = effective(await store.applied(), await store.staged())
        n = await store.staged_count()
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"config state error: {e}")
    return {"items": [_redact_item(i) for i in eff], "pending": n > 0, "count": n}

@router.put("/config/item", dependencies=[Depends(login_required)])
async def stage_item(body: dict = Body(...)):
    kind, name, data = body.get("kind"), body.get("name"), body.get("data")
    if not kind or not name: raise HTTPException(status_code=422, detail="kind and name required")
    if kind == "credential":
        api_key = (data or {}).get("api_key")
        if not api_key: raise HTTPException(status_code=422, detail="credential api_key required")
        data = {"provider": (data or {}).get("provider"),
                "value_encrypted": _fernet().encrypt(api_key.encode()).decode()}
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
        return await apply_config(s.config_path, make_config_store(), make_reloader(),
                                  decrypt=lambda b: f.decrypt(b.encode()).decode())
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
