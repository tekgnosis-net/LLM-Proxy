from __future__ import annotations
import httpx
from typing import Any, Optional
from urllib.parse import quote


class McpClient:
    """Async client for LiteLLM MCP-gateway admin endpoints (requires
    STORE_MODEL_IN_DB=true on the proxy). Master key stays server-side.
    GET /v1/mcp/server returns servers with credentials REDACTED (null) —
    live state is presence/content for non-secret fields only."""

    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def list_servers(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/v1/mcp/server")
            r.raise_for_status()
            data = r.json()
            return data.get("data", data) if isinstance(data, dict) else data

    async def add_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/v1/mcp/server", json=payload)
            r.raise_for_status()
            return r.json()

    async def update_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("server_id"):
            raise ValueError("update_server requires server_id")
        async with self._client() as c:
            r = await c.put(f"{self._base}/v1/mcp/server", json=payload)
            r.raise_for_status()
            return r.json()

    async def delete_server(self, server_id: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.delete(f"{self._base}/v1/mcp/server/{quote(str(server_id), safe='')}")
            r.raise_for_status()
            return r.json() if r.content else {}

    async def health(self, server_ids: list[str] | None = None) -> Any:
        params = [("server_ids", s) for s in (server_ids or [])]
        async with self._client() as c:
            r = await c.get(f"{self._base}/v1/mcp/server/health", params=params)
            r.raise_for_status()
            return r.json()

    async def list_tools(self, server_id: str) -> Any:
        async with self._client() as c:
            r = await c.get(f"{self._base}/mcp-rest/tools/list", params={"server_id": server_id})
            r.raise_for_status()
            return r.json()
