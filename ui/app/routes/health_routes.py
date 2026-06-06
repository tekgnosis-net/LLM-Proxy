from fastapi import APIRouter, Depends
from app.auth import login_required
from app.litellm_client import LitellmClient
from app.settings import get_settings

router = APIRouter(prefix="/api")


@router.get("/health", dependencies=[Depends(login_required)])
async def health():
    s = get_settings()
    client = LitellmClient(s.litellm_base_url, s.litellm_master_key)
    return {"ui": "ok", "proxy": await client.health()}
