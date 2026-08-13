import logging
from urllib.parse import urlparse
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from app.auth import login_required
from app.settings import get_settings
from app.mcp_client import McpClient
from app.routes.usage_routes import _iso_utc
from app.mcp_probe import probe_tools, ProbeError
from app.config_render import effective
from app.config_db import ConfigStore
from app.credentials_store import fernet_from_secret

router = APIRouter(prefix="/api")
log = logging.getLogger("uvicorn.error")


def make_mcp_client() -> McpClient:
    s = get_settings()
    return McpClient(s.litellm_base_url, s.litellm_master_key)


def make_preview_store() -> ConfigStore:
    return ConfigStore(get_settings().database_url)


def _preview_fernet():
    s = get_settings()
    return fernet_from_secret(s.credentials_key or s.session_secret)


def _origin(u: str) -> tuple:
    p = urlparse(u)
    return (p.scheme, p.hostname, p.port)


@router.get("/mcp/health", dependencies=[Depends(login_required)])
async def mcp_health(probe: int = 0, server_ids: str = ""):
    """Persisted per-server health (cheap, from the LiteLLM server table via list),
    plus an optional live probe (probe=1) which actively contacts each MCP server."""
    client = make_mcp_client()
    ids = [s for s in server_ids.split(",") if s] or None
    try:
        servers = await client.list_servers()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy MCP API error: {e}")
    out = {"servers": [{"server_id": s.get("server_id"), "server_name": s.get("server_name"),
                        "status": s.get("status"),
                        "last_health_check": _iso_utc_maybe(s.get("last_health_check")),
                        "health_check_error": s.get("health_check_error")}
                       for s in servers if ids is None or s.get("server_id") in ids]}
    if probe:
        try:
            out["probe"] = await client.health(ids)
        except Exception as e:
            out["probe_error"] = str(e)
    return out


def _iso_utc_maybe(v):
    """list_servers timestamps may arrive as ISO strings already — pass those through."""
    if v is None or isinstance(v, str):
        return v
    return _iso_utc(v)


@router.get("/mcp/tools", dependencies=[Depends(login_required)])
async def mcp_tools(server_id: str = Query(...)):
    try:
        return await make_mcp_client().list_tools(server_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy MCP API error: {e}")


@router.get("/mcp/usage", dependencies=[Depends(login_required)])
async def mcp_usage(days: int = 30):
    days = max(1, min(int(days), 365))
    dsn = get_settings().database_url
    if not dsn:
        return {"rows": []}
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT COALESCE(l.metadata::jsonb #>> '{mcp_tool_call_metadata,mcp_server_name}', '(unknown)') server, "
            "COUNT(*) calls, COALESCE(SUM(l.spend),0) spend, "
            "COUNT(*) FILTER (WHERE l.status='failure') failures, "
            'MAX(l."startTime") last_call '
            'FROM "LiteLLM_SpendLogs" l '
            "WHERE l.call_type = 'call_mcp_tool' "
            'AND l."startTime" > now() - make_interval(days => $1) '
            "GROUP BY server ORDER BY calls DESC", days)
    except Exception:
        log.exception("mcp_usage query failed (days=%s)", days)
        return {"rows": [], "error": "query_failed"}
    finally:
        await conn.close()
    return {"rows": [{"server": r["server"], "calls": r["calls"], "spend": float(r["spend"] or 0),
                      "failures": r["failures"], "last_call": _iso_utc(r["last_call"])} for r in rows]}


@router.post("/mcp/tools/preview", dependencies=[Depends(login_required)])
async def mcp_tools_preview(body: dict = Body(...)):
    """List an MCP server's tools by probing it DIRECTLY (works before the server
    is saved/applied — LiteLLM can only list tools for registered servers).
    Blank auth_value + server_id reuses the stored ciphertext (blank-means-keep parity)."""
    url = (body.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="url required (http:// or https://)")
    transport = body.get("transport") or "http"
    auth_type = body.get("auth_type") or None
    auth_value = body.get("auth_value") or ""
    if auth_type and not auth_value:
        server_id = body.get("server_id")
        dsn = get_settings().database_url
        if not (server_id and dsn):
            raise HTTPException(status_code=422, detail="auth_value required")
        store = make_preview_store()
        eff = effective(await store.applied(), await store.staged())
        existing = next((i for i in eff if i["kind"] == "mcp_server" and i["name"] == server_id
                         and i.get("flag") != "deleted"), None)
        ve = (existing.get("data") or {}).get("auth_value_encrypted") if existing else None
        if not ve:
            raise HTTPException(status_code=422, detail="auth_value required (no stored secret to reuse)")
        # Confused-deputy guard: a stored secret is only ever sent to the origin it
        # was saved for — probing a different host requires re-entering the secret.
        stored_url = (existing.get("data") or {}).get("url") or ""
        if _origin(url) != _origin(stored_url):
            raise HTTPException(status_code=422,
                                detail="url host differs from the stored server's — re-enter the auth value to probe a different host")
        try:
            auth_value = _preview_fernet().decrypt(ve.encode()).decode()
        except Exception:
            # never surface crypto errors (or ciphertext) — key rotation/corruption lands here
            raise HTTPException(status_code=422,
                                detail="stored secret could not be decrypted — re-enter the auth value")
    try:
        tools = await probe_tools(url, auth_type=auth_type, auth_value=auth_value,
                                  static_headers=body.get("static_headers") or {},
                                  transport=transport)
    except ProbeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        # never echo exception text: it can carry the URL (which can embed keys)
        raise HTTPException(status_code=502, detail=f"probe failed: {e.__class__.__name__}")
    return {"tools": tools}
