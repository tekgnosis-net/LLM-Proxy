from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.credentials_store import CredentialsStore, materialize_credentials, fernet_from_secret
from app.config_store import load_config, write_config, pending_status, seed_baseline_if_missing
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_credentials_store() -> CredentialsStore:
    s = get_settings()
    if not s.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return CredentialsStore(s.database_url, fernet_from_secret(s.credentials_key or s.session_secret))


async def _rematerialize_and_stage():
    """Re-render the vault into config.yaml (staged) so a later Apply picks it up."""
    s = get_settings()
    store = make_credentials_store()
    decrypted = await store.list_decrypted()
    current = load_config(s.config_path).model_dump(exclude_none=True)
    seed_baseline_if_missing(s.config_path)
    write_config(s.config_path, materialize_credentials(current, decrypted))
    return pending_status(s.config_path)


def _models_using_credential(config_path: str, name: str) -> list[str]:
    """Model names whose litellm_params reference the given credential by name."""
    try:
        cfg = load_config(config_path).model_dump(exclude_none=True)
    except Exception:
        return []
    return [m.get("model_name", "?") for m in (cfg.get("model_list") or [])
            if (m.get("litellm_params") or {}).get("litellm_credential_name") == name]


@router.get("/credentials", dependencies=[Depends(login_required)])
async def list_credentials():
    try:
        return await make_credentials_store().list_masked()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"credentials error: {e}")


@router.post("/credentials", dependencies=[Depends(login_required)])
async def create_credential(body: dict = Body(...)):
    name, prov, key = body.get("credential_name"), body.get("provider"), body.get("api_key")
    if not name or not key:
        raise HTTPException(status_code=422, detail="credential_name and api_key required")
    try:
        await make_credentials_store().create(name, prov, key)
        return {"ok": True, **(await _rematerialize_and_stage())}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"credentials error: {e}")


@router.delete("/credentials/{name}", dependencies=[Depends(login_required)])
async def delete_credential(name: str):
    in_use = _models_using_credential(get_settings().config_path, name)
    if in_use:
        raise HTTPException(status_code=409,
                            detail=f"credential '{name}' is in use by model(s): {', '.join(in_use)}. "
                                   "Remove the reference first.")
    try:
        await make_credentials_store().delete(name)
        return {"ok": True, **(await _rematerialize_and_stage())}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"credentials error: {e}")
