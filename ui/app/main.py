from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.settings import get_settings
from app.routes import auth_routes, health_routes, keys_routes, usage_routes
from app.routes import housekeeping_routes, models_routes, catalog_routes
from app.routes import config_v3_routes, system_routes, logs_routes, mcp_routes

STATIC_DIR = Path(__file__).parent / "static"


class CachedStaticFiles(StaticFiles):
    """Serve HTML with no-cache so a new build is always picked up, while the
    content-hashed `assets/*` are immutable (cached forever). Without this the
    browser heuristically caches index.html and keeps serving stale JS after an
    update — e.g. an admin would never receive a UI bugfix without a hard refresh."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        ct = resp.headers.get("content-type", "")
        if ct.startswith("text/html"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path.startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


@asynccontextmanager
async def lifespan(app):
    import logging
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from datetime import datetime, timezone
    from app.db_admin import DbAdmin
    s = get_settings()
    sched = None

    # Bootstrap import: seed the config DB from config.yaml on first run (idempotent).
    # seed_applied() is a no-op if ui_config_applied already has rows, so this is safe
    # across every restart.
    if s.database_url:
        try:
            from app.config_store import load_config
            from app.config_import import split_config
            from app.config_db import ConfigStore
            from app.credentials_store import fernet_from_secret
            cfg = load_config(s.config_path).model_dump(exclude_none=True)
            f = fernet_from_secret(s.credentials_key or s.session_secret)
            enc = lambda v: f.encrypt((v or "").encode()).decode()
            items, passthrough = split_config(cfg, encrypt=enc)
            if passthrough:
                items.append({"kind": "passthrough", "name": "_", "data": passthrough})
            store = ConfigStore(s.database_url)
            await store.seed_applied(items)
            await store.migrate_model_identities()
        except Exception:
            logging.getLogger(__name__).warning(
                "bootstrap import failed — DB may be temporarily unavailable; will retry on next restart",
                exc_info=True,
            )

    if s.housekeeping_enabled and s.database_url:
        sched = AsyncIOScheduler()

        async def job():
            try:
                await DbAdmin(s.database_url).run_maintenance(
                    s.housekeeping_spendlog_retention_days, s.housekeeping_delete_expired_keys)
            except Exception:
                pass   # cron is best-effort; manual run surfaces errors

        sched.add_job(job, "interval", hours=s.housekeeping_interval_hours, id="housekeeping")

    if s.catalog_sync_enabled and s.database_url:
        from app.catalog import Catalog
        sched = sched or AsyncIOScheduler()

        async def catalog_job():
            try:
                await Catalog(s.database_url, s.catalog_pricing_url, s.catalog_endpoints_url).sync()
            except Exception:
                pass   # cron is best-effort; fetch failures keep last-good data

        sched.add_job(catalog_job, "interval", days=s.catalog_sync_interval_days, id="catalog",
                      next_run_time=datetime.now(timezone.utc))

    if s.database_url:
        from app.backup_store import BackupStore, read_mirror, write_mirror
        from app.backup_scheduler import register_backup_jobs, set_scheduler
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        bstore = BackupStore(s.database_url)
        try:
            # Self-heal: ui_settings empty + mirror present (e.g. after a ui_* wipe) → re-import.
            if not await bstore.settings_present():
                mirror = read_mirror(s.backup_dir)
                if mirror:
                    for tier in ("config", "logs"):
                        if tier in mirror:
                            await bstore.save_settings(tier, mirror[tier])
                    logging.getLogger(__name__).warning("backup settings restored from %s/settings.json", s.backup_dir)
            sched = sched or AsyncIOScheduler()
            from app.routes.backup_routes import make_backup_engine
            await register_backup_jobs(sched, bstore, make_backup_engine)
            set_scheduler(sched)
        except Exception:
            logging.getLogger(__name__).warning("backup scheduler setup failed", exc_info=True)

    if sched:
        sched.start()
    try:
        yield
    finally:
        if sched:
            sched.shutdown(wait=False)


def create_app() -> FastAPI:
    s = get_settings()
    from app.config_store import seed_config_from_example
    seed_config_from_example(s.config_path)
    app = FastAPI(title="LLM Proxy UI", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret,
                       same_site="lax", https_only=s.session_cookie_secure)
    app.include_router(auth_routes.router)
    app.include_router(health_routes.router)
    app.include_router(config_v3_routes.router)
    app.include_router(keys_routes.router)
    app.include_router(usage_routes.router)
    app.include_router(housekeeping_routes.router)
    app.include_router(models_routes.router)
    app.include_router(mcp_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(system_routes.router)
    app.include_router(logs_routes.router)
    if STATIC_DIR.exists():
        app.mount("/", CachedStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
