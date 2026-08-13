from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.keys_client import KeysClient
from app.settings import get_settings
from app.config_db import ConfigStore
from app.config_render import effective
from app.config_integrity import group_names, _LITELLM_SPECIAL_MODELS, mga_names_from, mcp_server_names

router = APIRouter(prefix="/api")


def make_keys_client() -> KeysClient:
    s = get_settings()
    return KeysClient(s.litellm_base_url, s.litellm_master_key)


def make_config_store() -> ConfigStore:
    s = get_settings()
    return ConfigStore(s.database_url)


async def _validate_key_refs(payload: dict) -> None:
    """Reject a key whose models/aliases name a group that does not exist.
    An alias NAME may legitimately appear in models (the #25281 injection).
    Special tokens (all-*-models, no-default-models) are always allowed.
    Malformed payloads (non-str/non-dict entries) are skipped, not rejected."""
    s = get_settings()
    if not s.database_url:
        return                                        # no config store → skip (non-DB dev)
    store = make_config_store()
    eff = effective(await store.applied(), await store.staged())
    groups = group_names([i for i in eff if i["kind"] == "model"],
                         mga_names_from([i for i in eff if i["kind"] == "router_setting" and i.get("flag") != "deleted"]))
    _al = payload.get("aliases")
    alias_names = set(_al.keys()) if isinstance(_al, dict) else set()
    bad = [m for m in (payload.get("models") or [])
           if isinstance(m, str) and m and m not in groups
           and m not in alias_names and m not in _LITELLM_SPECIAL_MODELS]
    bad += [t for t in ((_al if isinstance(_al, dict) else {}).values())
            if isinstance(t, str) and t and t not in groups]
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"key references unknown model group(s): {', '.join(sorted(set(bad)))}")
    op = payload.get("object_permission")
    if isinstance(op, dict):
        valid = mcp_server_names([i for i in eff if i["kind"] == "mcp_server"])
        bad_mcp = [s for s in (op.get("mcp_servers") or [])
                   if isinstance(s, str) and s and s not in valid]
        if bad_mcp:
            raise HTTPException(status_code=422,
                                detail=f"key references unknown MCP server(s): {', '.join(sorted(set(bad_mcp)))}")


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
