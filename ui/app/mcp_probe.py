from __future__ import annotations
import json
from typing import Any, Optional
import httpx

# Streamable-HTTP MCP probe: lets the UI list a server's tools BEFORE it is
# registered with LiteLLM (LiteLLM can only list tools for applied servers).
# Wire behavior live-verified against deepwiki 2.14.3 (2026-08-13): responses may
# be SSE-framed or plain JSON; mcp-session-id is optional.

_RPC_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}
_PROTOCOL_VERSION = "2025-03-26"


class ProbeError(RuntimeError):
    """Human-readable probe failure — message is safe to show in the UI."""


def build_probe_headers(auth_type, auth_value, static_headers) -> dict:
    """Exact LiteLLM auth mapping (experimental_mcp_client/client.py:351-376):
    bearer_token → Authorization: Bearer; basic → Authorization: Basic <verbatim,
    NOT base64'd here>; api_key → X-API-Key."""
    headers = dict(_RPC_HEADERS)
    for k, v in (static_headers or {}).items():
        headers[str(k)] = str(v)
    if auth_type and auth_value:
        if auth_type == "bearer_token":
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "basic":
            headers["Authorization"] = f"Basic {auth_value}"
        elif auth_type == "api_key":
            headers["X-API-Key"] = auth_value
    return headers


def parse_rpc_response(r: httpx.Response) -> Optional[dict]:
    """JSON-RPC payload from a streamable-HTTP response: SSE-framed (first data:
    line) or plain JSON. None for empty bodies (notification ACKs)."""
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in r.text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except Exception as e:
                    raise ProbeError(f"malformed SSE frame from server: {e.__class__.__name__}") from e
        return None
    if not r.content:
        return None
    try:
        return r.json()
    except Exception as e:
        raise ProbeError(f"malformed JSON from server: {e.__class__.__name__}") from e


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code in (401, 403):
        raise ProbeError(f"server returned {r.status_code} — check the auth type/value")
    if r.status_code >= 400:
        raise ProbeError(f"server returned HTTP {r.status_code}")


def _raise_for_rpc_error(msg) -> None:
    if isinstance(msg, dict) and msg.get("error"):
        e = msg["error"]
        detail = e.get("message") if isinstance(e, dict) else str(e)
        raise ProbeError(f"server error: {detail}")


async def probe_tools(url, auth_type=None, auth_value=None, static_headers=None,
                      transport="http", timeout=10.0,
                      http_transport: Optional[httpx.BaseTransport] = None) -> list[dict[str, Any]]:
    if transport != "http":
        raise ProbeError("direct preview supports the HTTP transport only — Apply the "
                         "server first and use the Tools browser, or type the tool names")
    headers = build_probe_headers(auth_type, auth_value, static_headers)
    async with httpx.AsyncClient(timeout=timeout, transport=http_transport) as c:
        try:
            r = await c.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                           "clientInfo": {"name": "llm-proxy-ui", "version": "1.0"}}})
            _raise_for_status(r)
            _raise_for_rpc_error(parse_rpc_response(r))
            sid = r.headers.get("mcp-session-id")
            if sid:
                headers["mcp-session-id"] = sid
            r2 = await c.post(url, headers=headers,
                              json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            _raise_for_status(r2)
            r3 = await c.post(url, headers=headers,
                              json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            _raise_for_status(r3)
            msg = parse_rpc_response(r3)
            _raise_for_rpc_error(msg)
        except ProbeError:
            raise
        except httpx.TimeoutException as e:
            raise ProbeError("server did not respond in time") from e
        # class name only: httpx error strings embed the URL, which can carry keys
        except httpx.HTTPError as e:
            raise ProbeError(f"could not reach the server: {e.__class__.__name__}") from e
    tools = (((msg or {}).get("result") or {}).get("tools")) or []
    return [{"name": t.get("name", ""), "description": t.get("description") or ""}
            for t in tools if isinstance(t, dict) and t.get("name")]
