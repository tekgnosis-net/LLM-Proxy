from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.keys_client import KeysClient
from app.settings import get_settings
from app.config_db import ConfigStore
from app.config_render import effective
from app.config_integrity import group_names

router = APIRouter(prefix="/api")


def make_keys_client() -> KeysClient:
    s = get_settings()
    return KeysClient(s.litellm_base_url, s.litellm_master_key)


def make_config_store() -> ConfigStore:
    s = get_settings()
    return ConfigStore(s.database_url)


async def _validate_key_refs(payload: dict) -> None:
    """Reject a key whose models/aliases name a group that does not exist.
    An alias NAME may legitimately appear in models (the #25281 injection)."""
    s = get_settings()
    if not s.database_url:
        return                                        # no config store → skip (non-DB dev)
    store = make_config_store()
    eff = effective(await store.applied(), await store.staged())
    groups = group_names([i for i in eff if i["kind"] == "model"])
    alias_names = set((payload.get("aliases") or {}).keys())
    bad = [m for m in (payload.get("models") or []) if m and m not in groups and m not in alias_names]
    bad += [t for t in (payload.get("aliases") or {}).values() if t not in groups]
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"key references unknown model group(s): {', '.join(sorted(set(bad)))}")


@router.get("/keys", dependencies=[Depends(login_required)])
async def list_keys():
    try:
        return await make_keys_client().list_keys()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")


@router.post("/keys", dependencies=[Depends(login_required)])
async def create_key(payload: dict = Body(...)):
    await _validate_key_refs(payload)
    try:
        return await make_keys_client().generate_key(payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")


@router.post("/keys/update", dependencies=[Depends(login_required)])
async def update_key(payload: dict = Body(...)):
    await _validate_key_refs(payload)
    try:
        return await make_keys_client().update_key(payload)
    except HTTPException:
        raise
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
