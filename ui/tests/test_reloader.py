import httpx
import pytest
from app.reloader import Reloader, ReloadError


def _reloader(handler, mode="restart"):
    transport = httpx.MockTransport(handler)
    return Reloader(
        socket_proxy_url="http://socket-proxy:2375",
        litellm_base_url="http://litellm:4000",
        master_key="sk-test",
        container="litellm-proxy",
        mode=mode,
        transport=transport,
        poll_interval_s=0.0,
        timeout_s=0.5,
    )


@pytest.mark.asyncio
async def test_verify_continues_through_connection_error_then_succeeds():
    """Restart window: first probes raise ConnectError (container down), then healthy.
    The loop MUST keep polling through the down-period, not abort."""
    calls = {"n": 0}
    def handler(req):
        if req.url.path.endswith("/restart"):
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("container down")
            return httpx.Response(200, json={"status": "healthy"})
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "cheap"}]})
        return httpx.Response(404)
    r = _reloader(handler, mode="restart")
    assert await r.reload_and_verify(expected_models=["cheap"]) is True
    assert calls["n"] >= 3


@pytest.mark.asyncio
async def test_restart_mode_hits_restart_endpoint_and_verifies():
    seen = {}
    def handler(req):
        if req.url.path.endswith("/restart"):
            seen["restart"] = True
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            return httpx.Response(200, json={"status": "healthy", "db": "connected"})
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "cheap"}, {"id": "smart"}]})
        return httpx.Response(404)
    r = _reloader(handler, mode="restart")
    assert await r.reload_and_verify(expected_models=["cheap"]) is True
    assert seen.get("restart") is True


@pytest.mark.asyncio
async def test_sighup_mode_hits_kill_endpoint():
    seen = {}
    def handler(req):
        if req.url.path.endswith("/kill"):
            seen["signal"] = req.url.params.get("signal")
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            return httpx.Response(200, json={"status": "healthy"})
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)
    r = _reloader(handler, mode="SIGHUP")
    assert await r.reload_and_verify(expected_models=[]) is True
    assert seen.get("signal") == "SIGHUP"


@pytest.mark.asyncio
async def test_reload_fails_when_model_missing():
    def handler(req):
        if req.url.path.endswith("/restart"):
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            return httpx.Response(200, json={"status": "healthy"})
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "cheap"}]})
        return httpx.Response(404)
    r = _reloader(handler)
    with pytest.raises(ReloadError):
        await r.reload_and_verify(expected_models=["smart"])


@pytest.mark.asyncio
async def test_reload_fails_when_unhealthy():
    def handler(req):
        if req.url.path.endswith("/restart"):
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            return httpx.Response(503, json={"status": "unhealthy"})
        return httpx.Response(200, json={"data": []})
    r = _reloader(handler)
    with pytest.raises(ReloadError):
        await r.reload_and_verify(expected_models=[])


@pytest.mark.asyncio
async def test_trigger_uses_generous_timeout_not_probe_timeout():
    """Regression: the docker /restart POST blocks ~25s, so it must use the long
    trigger_timeout, NOT the 10s probe timeout — otherwise the POST aborts mid-restart
    and Apply returns 500 even though the restart + config commit already succeeded."""
    def handler(req):
        if req.url.path.endswith("/restart"):
            return httpx.Response(204)
        return httpx.Response(404)
    r = _reloader(handler)
    r._trigger_timeout = 55.0
    seen = []
    orig = r._client
    def spy(timeout=10.0):
        seen.append(timeout)
        return orig(timeout)
    r._client = spy
    await r.trigger()
    assert seen == [55.0]   # trigger used the long timeout, not the 10s probe default


@pytest.mark.asyncio
async def test_trigger_error_raises_reloaderror():
    def handler(req):
        if req.url.path.endswith("/restart"):
            return httpx.Response(500, text="boom")
        return httpx.Response(404)
    r = _reloader(handler)
    with pytest.raises(ReloadError):
        await r.reload_and_verify(expected_models=[])
