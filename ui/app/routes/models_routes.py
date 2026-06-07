from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.litellm_client import LitellmClient
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_models_client() -> LitellmClient:
    s = get_settings()
    return LitellmClient(s.litellm_base_url, s.litellm_master_key)


@router.post("/models/test", dependencies=[Depends(login_required)])
async def test_model(body: dict = Body(...)):
    lp = body.get("litellm_params") or {}
    if not lp.get("model"):
        raise HTTPException(status_code=422, detail="litellm_params.model required")
    try:
        return await make_models_client().test_connection(lp, body.get("mode", "chat"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"test failed: {e}")


@router.get("/models/health", dependencies=[Depends(login_required)])
async def models_health():
    try:
        return await make_models_client().health_all()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"health error: {e}")
