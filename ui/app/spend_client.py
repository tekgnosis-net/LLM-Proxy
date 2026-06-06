from __future__ import annotations
import httpx
from typing import Any, Optional


class SpendClient:
    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self):
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def total_spend(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/spend"); r.raise_for_status(); return r.json()

    async def spend_by_model(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/spend/models", params={"limit": limit})
            r.raise_for_status(); d = r.json(); return d if isinstance(d, list) else []

    async def spend_by_key(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/spend/keys", params={"limit": limit})
            r.raise_for_status(); d = r.json(); return d if isinstance(d, list) else []

    async def activity(self, start_date: str, end_date: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/activity", params={"start_date": start_date, "end_date": end_date})
            r.raise_for_status(); return r.json()
