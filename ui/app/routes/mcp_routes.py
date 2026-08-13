import logging
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import login_required
from app.settings import get_settings
from app.mcp_client import McpClient
from app.routes.usage_routes import _iso_utc

router = APIRouter(prefix="/api")
log = logging.getLogger("uvicorn.error")


def make_mcp_client() -> McpClient:
    s = get_settings()
    return McpClient(s.litellm_base_url, s.litellm_master_key)


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
