from __future__ import annotations
import httpx
from typing import Any, Optional


class LitellmClient:
    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=8.0, transport=self._transport)

    async def health(self) -> dict[str, Any]:
        try:
            async with self._client() as c:
                r = await c.get(f"{self._base}/health/readiness")
                return {"reachable": True, "status_code": r.status_code, "raw": r.json()}
        except (httpx.HTTPError, ValueError) as e:
            return {"reachable": False, "error": str(e)}
