from fastapi import APIRouter, Depends, HTTPException
from app.auth import login_required
from app.config_store import load_config, ConfigError
from app.settings import get_settings

router = APIRouter(prefix="/api")


@router.get("/config", dependencies=[Depends(login_required)])
def get_config():
    s = get_settings()
    try:
        cfg = load_config(s.config_path)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return cfg.model_dump(exclude_none=True)
