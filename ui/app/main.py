from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.settings import get_settings
from app.routes import auth_routes, health_routes, config_routes, keys_routes, usage_routes
from app.routes import housekeeping_routes, credentials_routes

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from app.db_admin import DbAdmin
    s = get_settings()
    sched = None
    if s.housekeeping_enabled and s.database_url:
        sched = AsyncIOScheduler()

        async def job():
            try:
                await DbAdmin(s.database_url).run_maintenance(
                    s.housekeeping_spendlog_retention_days, s.housekeeping_delete_expired_keys)
            except Exception:
                pass   # cron is best-effort; manual run surfaces errors

        sched.add_job(job, "interval", hours=s.housekeeping_interval_hours, id="housekeeping")
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
                       same_site="lax", https_only=False)
    app.include_router(auth_routes.router)
    app.include_router(health_routes.router)
    app.include_router(config_routes.router)
    app.include_router(keys_routes.router)
    app.include_router(usage_routes.router)
    app.include_router(housekeeping_routes.router)
    app.include_router(credentials_routes.router)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
