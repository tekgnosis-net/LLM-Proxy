from pathlib import Path
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from app.auth import login_required
from app.config_store import load_config, ConfigError, write_config, pending_status, seed_baseline_if_missing
from app.settings import get_settings
from app.apply import apply_config, ApplyError
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


@router.get("/config/export", dependencies=[Depends(login_required)])
def export_config():
    s = get_settings()
    try:
        text = Path(s.config_path).read_text()
    except OSError as e:
        raise HTTPException(status_code=404, detail=f"config not found: {e}")
    return PlainTextResponse(text, media_type="text/yaml",
                             headers={"Content-Disposition": 'attachment; filename="config.yaml"'})


@router.put("/config", dependencies=[Depends(login_required)])
def put_config(raw: dict = Body(...)):
    s = get_settings()
    seed_baseline_if_missing(s.config_path)     # capture pre-write state as baseline if first save
    try:
        write_config(s.config_path, raw)        # validate + stage (NO restart)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, **pending_status(s.config_path)}


@router.get("/apply/status", dependencies=[Depends(login_required)])
def apply_status():
    return pending_status(get_settings().config_path)


@router.post("/apply", dependencies=[Depends(login_required)])
async def apply():
    s = get_settings()
    try:
        return await apply_config(s.config_path, make_reloader())
    except ApplyError as e:
        code = 422 if "invalid" in str(e) else 409     # apply_config says "config invalid…" or "reload failed…"
        raise HTTPException(status_code=code, detail=str(e))
