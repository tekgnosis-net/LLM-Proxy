from __future__ import annotations
# NOTE: SIGHUP does NOT reload config on ghcr.io/berriai/litellm:main-stable
# (verified by spike) — default mode is "restart" (~25s). SIGHUP kept for images
# that support it.
import asyncio
import time
import httpx
from typing import Optional


class ReloadError(RuntimeError):
    pass


class Reloader:
    """Triggers a LiteLLM config reload via the scoped docker-socket-proxy, then
    verifies the proxy returns healthy AND serves the expected models. Raises
    ReloadError if it doesn't converge within timeout (caller rolls back)."""

    def __init__(self, socket_proxy_url, litellm_base_url, master_key, container,
                 mode="restart", transport: Optional[httpx.BaseTransport] = None,
                 poll_interval_s: float = 1.5, timeout_s: float = 90.0,
                 trigger_timeout_s: float = 60.0):
        self._sock = socket_proxy_url.rstrip("/")
        self._base = litellm_base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._container = container
        self._mode = mode
        self._transport = transport
        self._poll = poll_interval_s
        self._timeout = timeout_s
        self._trigger_timeout = trigger_timeout_s

    def _client(self, timeout: float = 10.0):
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def trigger(self) -> None:
        # The docker /restart call BLOCKS until the container has stopped+started
        # (~25s for litellm: SIGTERM grace + worker spin-up). It must NOT use the
        # 10s probe timeout — that aborts the POST mid-restart and makes Apply
        # return 500 even though the restart (and the already-committed config)
        # succeeded. Give the trigger its own generous timeout; probes stay short.
        async with self._client(self._trigger_timeout) as c:
            if self._mode == "SIGHUP":
                r = await c.post(f"{self._sock}/containers/{self._container}/kill",
                                 params={"signal": "SIGHUP"})
            else:
                r = await c.post(f"{self._sock}/containers/{self._container}/restart")
            if r.status_code >= 400:
                raise ReloadError(f"reload trigger failed: {r.status_code} {r.text[:200]}")

    async def stop(self) -> None:
        async with self._client(self._trigger_timeout) as c:
            r = await c.post(f"{self._sock}/containers/{self._container}/stop", params={"t": 30})
            if r.status_code >= 400 and r.status_code != 304:   # 304 = already stopped
                raise ReloadError(f"stop failed: {r.status_code} {r.text[:200]}")

    async def start(self) -> None:
        async with self._client(self._trigger_timeout) as c:
            r = await c.post(f"{self._sock}/containers/{self._container}/start")
            if r.status_code >= 400 and r.status_code != 304:   # 304 = already started
                raise ReloadError(f"start failed: {r.status_code} {r.text[:200]}")

    async def verify(self, expected_models: list[str]) -> bool:
        deadline = time.monotonic() + self._timeout
        last = "no probe yet"
        while time.monotonic() < deadline:
            try:
                async with self._client() as c:
                    h = await c.get(f"{self._base}/health/readiness", headers=self._headers)
                    if h.status_code == 200 and h.json().get("status") == "healthy":
                        m = await c.get(f"{self._base}/v1/models", headers=self._headers)
                        ids = {d.get("id") for d in (m.json().get("data") or [])} if m.status_code == 200 else set()
                        if set(expected_models).issubset(ids):
                            return True
                        last = f"models {sorted(ids)} missing {sorted(set(expected_models)-ids)}"
                    else:
                        last = f"health {h.status_code}"
            except httpx.HTTPError as e:
                last = f"probe error: {e}"
            if self._poll:
                await asyncio.sleep(self._poll)
        raise ReloadError(f"proxy did not converge within {self._timeout}s ({last})")

    async def reload_and_verify(self, expected_models: list[str]) -> bool:
        await self.trigger()
        return await self.verify(expected_models)
