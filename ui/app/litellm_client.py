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

    async def test_connection(self, litellm_params: dict, mode: str = "chat") -> dict:
        async with self._client() as c:
            r = await c.post(f"{self._base}/health/test_connection", json={"litellm_params": litellm_params, "mode": mode})
            return {"status": "success", "result": r.json()} if r.status_code < 400 else {"status": "error", "result": r.text}

    async def health_all(self) -> dict:
        async with self._client() as c:
            r = await c.get(f"{self._base}/health"); r.raise_for_status(); return r.json()
