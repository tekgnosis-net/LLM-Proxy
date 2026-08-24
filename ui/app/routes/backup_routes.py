# ui/app/routes/backup_routes.py
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth import login_required
from app.settings import get_settings
from app.backup_engine import BackupEngine
from app.backup_store import BackupStore, TIERS, validate_tier_settings, write_mirror
from app.backup_restore import (parse_export, rollback_preview, check_decryptable,
                                rollback_config, full_recovery, restore_logs)
from app.backup_scheduler import register_backup_jobs, get_scheduler, next_fire
from app.config_db import ConfigStore
from app.credentials_store import fernet_from_secret
from app.models_client import ModelsClient
from app.mcp_client import McpClient
from app.reloader import Reloader

log = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api")


def make_backup_store() -> BackupStore:
    s = get_settings()
    if not s.database_url: raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return BackupStore(s.database_url)


def make_config_store() -> ConfigStore:
    return ConfigStore(get_settings().database_url)


def make_models_client() -> ModelsClient:
    s = get_settings(); return ModelsClient(s.litellm_base_url, s.litellm_master_key)


def make_mcp_client() -> McpClient:
    s = get_settings(); return McpClient(s.litellm_base_url, s.litellm_master_key)


def make_reloader() -> Reloader:
    s = get_settings()
    return Reloader(s.socket_proxy_url, s.litellm_base_url, s.litellm_master_key,
                    s.litellm_container, mode=s.reload_mode, timeout_s=s.reload_timeout_s)


def make_backup_engine() -> BackupEngine:
    s = get_settings()
    return BackupEngine(s.database_url, s.backup_dir, make_backup_store(),
                        make_config_store(), s.config_path,
                        fernet_secret=(s.credentials_key or s.session_secret), salt_key=None)


def _fernet():
    s = get_settings(); return fernet_from_secret(s.credentials_key or s.session_secret)


def _interval_days(tier_settings: dict) -> int:
    f = tier_settings["frequency"]
    return {"daily": 1, "weekly": 7}.get(f["kind"], f.get("n", 1))


def _bid_path(bid: str) -> Path:
    try:
        return make_backup_engine().backup_path(bid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def _require_confirm(body: dict, word: str) -> None:
    if body.get("confirm") != word:
        raise HTTPException(status_code=422, detail=f'confirmation required: pass confirm:"{word}"')


@router.get("/backup/status", dependencies=[Depends(login_required)])
async def backup_status():
    store = make_backup_store()
    settings = await store.get_settings()
    eng = make_backup_engine()
    tiers = {}
    now = datetime.now().astimezone()
    for t in TIERS:
        last_ok = await store.last_run(t, "ok")
        last_err = await store.last_run(t, "error")
        stale = False
        if settings[t]["enabled"]:
            window = timedelta(days=2 * _interval_days(settings[t]))
            stale = (last_ok is None or
                     datetime.fromisoformat(last_ok["finished_at"]) < now - window)
        tiers[t] = {"enabled": settings[t]["enabled"], "last_ok": last_ok,
                    "last_error": (last_err or {}).get("error"),
                    "running": eng.running.get(t, False),
                    "next_run": next_fire(t), "stale": stale}
    master = live = None
    try:
        master = len([i for i in await make_config_store().applied() if i["kind"] == "model"])
    except Exception:
        pass
    try:
        live = len(await make_models_client().list_models())
    except Exception:
        pass
    return {"tiers": tiers, "master_models": master, "live_models": live,
            "master_empty_live_nonempty": bool(master == 0 and (live or 0) > 0)}


@router.get("/backup/settings", dependencies=[Depends(login_required)])
async def backup_settings_get():
    return await make_backup_store().get_settings()


@router.put("/backup/settings", dependencies=[Depends(login_required)])
async def backup_settings_put(body: dict = Body(...)):
    store = make_backup_store()
    try:
        for tier in TIERS:
            if tier in body:
                await store.save_settings(tier, validate_tier_settings(body[tier]))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    settings = await store.get_settings()
    try:
        write_mirror(get_settings().backup_dir, settings)
    except OSError as e:
        log.warning("could not write settings mirror: %s", e)
    sched = get_scheduler()
    if sched is not None:
        await register_backup_jobs(sched, store, make_backup_engine)
    return settings


@router.post("/backup/run", dependencies=[Depends(login_required)])
async def backup_run(body: dict = Body(...)):
    tier = body.get("tier")
    if tier not in TIERS:
        raise HTTPException(status_code=422, detail="tier must be config|logs")
    eng = make_backup_engine()
    out = await (eng.run_config() if tier == "config" else eng.run_logs())
    if not out.get("ok") and out.get("error") == "already running":
        raise HTTPException(status_code=409, detail="a backup for this tier is already running")
    return out


@router.get("/backup/list", dependencies=[Depends(login_required)])
async def backup_list():
    out = make_backup_engine().list_backups()
    out["runs"] = await make_backup_store().runs(limit=40)
    return out


def _load_source_items(bid: str) -> list[dict]:
    p = _bid_path(bid)
    if bid.startswith("config/"):
        p = p / "ui_config.json"
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"{bid}: ui_config.json not found")
    try:
        return parse_export(p.read_text())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/backup/rollback/preview", dependencies=[Depends(login_required)])
async def backup_rollback_preview(source: str):
    items = _load_source_items(source)
    current = await make_config_store().applied()
    out = rollback_preview(current, items)
    out["undecryptable"] = check_decryptable(items, _fernet())
    return out


@router.post("/backup/rollback", dependencies=[Depends(login_required)])
async def backup_rollback(body: dict = Body(...)):
    _require_confirm(body, "ROLLBACK")
    items = _load_source_items(body.get("source", ""))
    bad = check_decryptable(items, _fernet())
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"cannot decrypt with current secret: {', '.join(bad)}")
    s = get_settings()
    return await rollback_config(items, config_store=make_config_store(),
                                 models_client=make_models_client(), mcp_client=make_mcp_client(),
                                 reloader=make_reloader(), config_path=s.config_path,
                                 fernet=_fernet())


@router.post("/backup/recover", dependencies=[Depends(login_required)])
async def backup_recover(body: dict = Body(...)):
    _require_confirm(body, "RECOVER")
    bid = body.get("source", "")
    if not bid.startswith("config/"):
        raise HTTPException(status_code=422, detail="full recovery needs a config-tier backup")
    p = _bid_path(bid)
    if not p.is_dir():
        raise HTTPException(status_code=404, detail="backup not found")
    s = get_settings()
    import asyncpg
    from app.backup_engine import _default_run_subprocess
    out = await full_recovery(p, dsn=s.database_url, reloader=make_reloader(),
                              config_path=s.config_path,
                              connect=lambda: asyncpg.connect(s.database_url),
                              run_subprocess=_default_run_subprocess,
                              salt_key=None, fernet_secret=(s.credentials_key or s.session_secret))
    return out


@router.post("/backup/restore-logs", dependencies=[Depends(login_required)])
async def backup_restore_logs(body: dict = Body(...)):
    _require_confirm(body, "MERGE")
    src = body.get("source", "")
    eng = make_backup_engine()
    if src == "all":
        dirs = [eng.backup_path(b["id"]) for b in eng.list_backups()["backups"]
                if b["tier"] == "logs"]
        dirs.sort()
    else:
        if not src.startswith("logs/"):
            raise HTTPException(status_code=422, detail="restore-logs needs a logs-tier backup or 'all'")
        p = _bid_path(src)
        if not p.is_dir(): raise HTTPException(status_code=404, detail="backup not found")
        dirs = [p]
    import asyncpg
    s = get_settings()
    return await restore_logs(dirs, lambda: asyncpg.connect(s.database_url))


@router.get("/backup/download", dependencies=[Depends(login_required)])
async def backup_download(path: str):
    p = _bid_path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(p, filename=p.name)


@router.delete("/backup/item", dependencies=[Depends(login_required)])
async def backup_delete(body: dict = Body(...)):
    import shutil
    bid = body.get("path", "")
    p = _bid_path(bid)
    if p.is_dir() and (p / "manifest.json").is_file():
        shutil.rmtree(p)
    elif bid.startswith("snapshots/") and p.is_file() and p.suffix == ".json":
        p.unlink()
    else:
        raise HTTPException(status_code=404, detail="not a deletable backup")
    return {"ok": True}
