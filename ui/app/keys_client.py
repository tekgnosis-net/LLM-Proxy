from __future__ import annotations
import httpx
from typing import Any, Optional


class KeysClient:
    """Async client for LiteLLM key-management endpoints. Master key stays here
    (server-side); never returned to the browser except the one-time plaintext
    from generate()."""

    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def list_keys(self, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/key/list",
                            params={"return_full_object": "true", "page": page, "size": size})
            r.raise_for_status()
            data = r.json()
            keys = data.get("keys", data) if isinstance(data, dict) else data
            # normalize: keep only dict items (full objects); drop bare token strings
            return [k for k in keys if isinstance(k, dict)]

    async def generate_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/key/generate", json=payload)
            r.raise_for_status()
            return r.json()

    async def delete_keys(self, tokens: list[str]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/key/delete", json={"keys": tokens})
            r.raise_for_status()
            return r.json()
