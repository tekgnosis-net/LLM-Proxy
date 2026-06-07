from pathlib import Path
import yaml
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from app.auth import login_required
from app.config_store import load_config, ConfigError, write_config, pending_status, seed_baseline_if_missing, restore_baseline
from app.credentials_store import materialize_credentials
from app.settings import get_settings
from app.apply import apply_config, ApplyError
from app.reloader import Reloader

router = APIRouter(prefix="/api")


def _redact(cfg: dict) -> dict:
    """Mask credential_values in credential_list so secrets are never sent to the browser."""
    cl = cfg.get("credential_list")
    if isinstance(cl, list):
        cfg = {**cfg, "credential_list": [
            {**c, "credential_values": {k: "***" for k in (c.get("credential_values") or {})}}
            for c in cl
        ]}
    return cfg


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
    return _redact(cfg.model_dump(exclude_none=True))


@router.get("/config/export", dependencies=[Depends(login_required)])
def export_config():
    s = get_settings()
    try:
        raw = yaml.safe_load(Path(s.config_path).read_text()) or {}
    except OSError as e:
        raise HTTPException(status_code=404, detail=f"config not found: {e}")
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"invalid YAML: {e}")
    redacted = _redact(raw if isinstance(raw, dict) else {})
    text = yaml.safe_dump(redacted, sort_keys=False, default_flow_style=False)
    return PlainTextResponse(text, media_type="text/yaml",
                             headers={"Content-Disposition": 'attachment; filename="config.yaml"'})


@router.put("/config", dependencies=[Depends(login_required)])
async def put_config(raw: dict = Body(...)):
    s = get_settings()
    # Never trust client-sent credential_list (it's redacted in GET anyway); strip it and
    # re-inject from the vault so model saves never drop or expose credentials.
    raw = {k: v for k, v in raw.items() if k != "credential_list"}
    try:
        from app.routes.credentials_routes import make_credentials_store
        decrypted = await make_credentials_store().list_decrypted()
    except Exception:
        decrypted = []   # no DB or vault error → degrade gracefully (existing tests w/o DB still pass)
    seed_baseline_if_missing(s.config_path)     # capture pre-write state as baseline if first save
    try:
        write_config(s.config_path, materialize_credentials(raw, decrypted))   # validate + stage (NO restart)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, **pending_status(s.config_path)}


@router.get("/apply/status", dependencies=[Depends(login_required)])
def apply_status():
    return pending_status(get_settings().config_path)


@router.get("/cache/info", dependencies=[Depends(login_required)])
def cache_info():
    s = get_settings()
    cfg = load_config(s.config_path)
    cp = cfg.litellm_settings.cache_params
    return {"enabled": bool(cfg.litellm_settings.cache),
            "type": getattr(cp, "type", None) if cp else None,
            "ttl": getattr(cp, "ttl", None) if cp else None,
            "host": s.redis_host or "valkey", "port": s.redis_port or "6379"}


@router.post("/apply", dependencies=[Depends(login_required)])
async def apply():
    s = get_settings()
    try:
        return await apply_config(s.config_path, make_reloader())
    except ApplyError as e:
        code = 422 if "invalid" in str(e) else 409     # apply_config says "config invalid…" or "reload failed…"
        raise HTTPException(status_code=code, detail=str(e))


@router.post("/discard", dependencies=[Depends(login_required)])
def discard():
    s = get_settings()
    seed_baseline_if_missing(s.config_path)   # ensure a baseline exists (first-run no-op safety)
    restore_baseline(s.config_path)           # copy .applied.yaml -> config.yaml (no proxy restart needed)
    return pending_status(s.config_path)
