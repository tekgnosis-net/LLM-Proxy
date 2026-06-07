from fastapi import APIRouter, Depends, HTTPException
from app.auth import login_required
from app.catalog import Catalog
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_catalog() -> Catalog:
    s = get_settings()
    if not s.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return Catalog(s.database_url, s.catalog_pricing_url, s.catalog_endpoints_url)


@router.get("/catalog/model/{name:path}", dependencies=[Depends(login_required)])
async def catalog_model(name: str):
    try:
        m = await make_catalog().get_model(name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"catalog error: {e}")
    if not m:
        raise HTTPException(status_code=404, detail="model not in catalog")
    return m


@router.get("/catalog/providers", dependencies=[Depends(login_required)])
async def catalog_providers():
    try:
        return await make_catalog().get_providers()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"catalog error: {e}")


@router.get("/catalog/status", dependencies=[Depends(login_required)])
async def catalog_status():
    try:
        return await make_catalog().status()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"catalog error: {e}")


@router.post("/catalog/sync", dependencies=[Depends(login_required)])
async def catalog_sync():
    try:
        return await make_catalog().sync()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"catalog sync failed: {e}")
