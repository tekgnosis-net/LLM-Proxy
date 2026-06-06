from fastapi import APIRouter, Depends, HTTPException
from app.auth import login_required
from app.db_admin import DbAdmin
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_db_admin() -> DbAdmin:
    s = get_settings()
    if not s.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return DbAdmin(s.database_url)


def _settings_view():
    s = get_settings()
    return {"enabled": s.housekeeping_enabled, "interval_hours": s.housekeeping_interval_hours,
            "retention_days": s.housekeeping_spendlog_retention_days,
            "delete_expired_keys": s.housekeeping_delete_expired_keys}


@router.get("/housekeeping", dependencies=[Depends(login_required)])
async def housekeeping():
    try:
        stats = await make_db_admin().stats()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"DB error: {e}")
    return {"stats": stats, "settings": _settings_view()}


@router.post("/housekeeping/run", dependencies=[Depends(login_required)])
async def run_now():
    s = get_settings()
    try:
        return await make_db_admin().run_maintenance(s.housekeeping_spendlog_retention_days,
                                                      s.housekeeping_delete_expired_keys)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"DB error: {e}")
