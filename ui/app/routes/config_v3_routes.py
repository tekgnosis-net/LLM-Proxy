from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.settings import get_settings
from app.config_db import ConfigStore
from app.config_render import effective, render_config, redact_rendered
from app.config_engine import apply_config, pending_status, ApplyError
from app.credentials_store import fernet_from_secret
from app.config_store import ConfigError

router = APIRouter(prefix="/api")

def make_config_store() -> ConfigStore:
    s = get_settings()
    if not s.database_url: raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return ConfigStore(s.database_url)

def _fernet():
    s = get_settings(); return fernet_from_secret(s.credentials_key or s.session_secret)

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
