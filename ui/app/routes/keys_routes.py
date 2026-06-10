from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.keys_client import KeysClient
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_keys_client() -> KeysClient:
    s = get_settings()
    return KeysClient(s.litellm_base_url, s.litellm_master_key)


@router.get("/keys", dependencies=[Depends(login_required)])
async def list_keys():
    try:
        return await make_keys_client().list_keys()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")


@router.post("/keys", dependencies=[Depends(login_required)])
async def create_key(payload: dict = Body(...)):
    try:
        return await make_keys_client().generate_key(payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")


@router.post("/keys/update", dependencies=[Depends(login_required)])
async def update_key(payload: dict = Body(...)):
    try:
        return await make_keys_client().update_key(payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")


@router.post("/keys/delete", dependencies=[Depends(login_required)])
async def delete_keys(body: dict = Body(...)):
    tokens = body.get("tokens") or []
    if not tokens:
        raise HTTPException(status_code=422, detail="no tokens provided")
    try:
        return await make_keys_client().delete_keys(tokens)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")
