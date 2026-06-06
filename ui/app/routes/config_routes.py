from fastapi import APIRouter, Body, Depends, HTTPException
from app.auth import login_required
from app.config_store import load_config, ConfigError
from app.settings import get_settings
from app.safe_apply import safe_apply, SafeApplyError
from app.reloader import Reloader

router = APIRouter(prefix="/api")


def make_reloader() -> Reloader:
    s = get_settings()
    return Reloader(s.socket_proxy_url, s.litellm_base_url, s.litellm_master_key,
                    s.litellm_container, mode=s.reload_mode, timeout_s=s.reload_timeout_s)


@router.get("/config", dependencies=[Depends(login_required)])
def get_config():
    s = get_settings()
    try:
        cfg = load_config(s.config_path)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return cfg.model_dump(exclude_none=True)


@router.put("/config", dependencies=[Depends(login_required)])
async def put_config(raw: dict = Body(...)):
    s = get_settings()
    try:
        cfg = await safe_apply(s.config_path, raw, make_reloader())
    except SafeApplyError as e:
        code = 422 if "invalid config" in str(e) else 409
        raise HTTPException(status_code=code, detail=str(e))
    return {"ok": True, "models": [m.model_name for m in cfg.model_list],
            "routing_strategy": cfg.router_settings.routing_strategy}
