import httpx
import pytest
from app.litellm_client import LitellmClient


@pytest.mark.asyncio
async def test_health_ok():
    def handler(request):
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"status": "healthy", "db": "connected"})

    transport = httpx.MockTransport(handler)
    client = LitellmClient("http://litellm:4000", "sk-test", transport=transport)
    health = await client.health()
    assert health["reachable"] is True
    assert health["raw"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_unreachable():
    def handler(request):
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(handler)
    client = LitellmClient("http://litellm:4000", "sk-test", transport=transport)
    health = await client.health()
    assert health["reachable"] is False
