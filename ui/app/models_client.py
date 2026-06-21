from __future__ import annotations
import httpx
from typing import Any, Optional
from urllib.parse import quote
from app.model_content import normalized_managed


class ModelsClient:
    """Async client for LiteLLM model-management endpoints (requires
    STORE_MODEL_IN_DB=true on the proxy). Master key stays server-side."""

    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def list_models(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/model/info")
            r.raise_for_status()
            data = r.json()
            return data.get("data", data) if isinstance(data, dict) else data

    async def add_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/model/new", json=payload)
            r.raise_for_status()
            return r.json()

    async def update_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        # LiteLLM's old POST /model/update drops model_info; the PATCH endpoint
        # /model/{id}/update persists it. Overlay normalized_managed so UI-managed
        # model_info fields are explicit → PATCH-merge overwrites both ways.
        mid = (payload.get("model_info") or {}).get("id")
        if not mid:
            raise ValueError("update_model requires model_info.id")
        body = dict(payload)
        mi = dict(payload.get("model_info") or {})
        mi.update(normalized_managed(mi))
        body["model_info"] = mi
        async with self._client() as c:
            r = await c.patch(f"{self._base}/model/{quote(str(mid), safe='')}/update", json=body)
            r.raise_for_status()
            return r.json()

    async def delete_model(self, model_id: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/model/delete", json={"id": model_id})
            r.raise_for_status()
            return r.json()
