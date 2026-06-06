from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.settings import get_settings
from app.routes import auth_routes, health_routes, config_routes

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="LLM Proxy UI")
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret,
                       same_site="lax", https_only=False)
    app.include_router(auth_routes.router)
    app.include_router(health_routes.router)
    app.include_router(config_routes.router)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
