import httpx, pytest
from app.spend_client import SpendClient


def _c(handler):
    return SpendClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_total_spend():
    def h(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        return httpx.Response(200, json={"spend": 4.23, "max_budget": None})
    assert (await _c(h).total_spend())["spend"] == 4.23


@pytest.mark.asyncio
async def test_spend_by_model():
    def h(req):
        assert req.url.path.endswith("/global/spend/models")
        return httpx.Response(200, json=[{"model": "gpt-4o", "total_spend": 2.1}])
    rows = await _c(h).spend_by_model()
    assert rows[0]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_spend_by_key():
    def h(req):
        return httpx.Response(200, json=[{"api_key": "hash", "key_alias": "ci", "total_spend": 1.0}])
    rows = await _c(h).spend_by_key()
    assert rows[0]["key_alias"] == "ci"


@pytest.mark.asyncio
async def test_activity_passes_dates():
    seen = {}
    def h(req):
        seen["s"] = req.url.params.get("start_date"); seen["e"] = req.url.params.get("end_date")
        return httpx.Response(200, json={"daily_data": [{"date": "Jun 05", "api_requests": 10, "total_tokens": 100}], "sum_api_requests": 10, "sum_total_tokens": 100})
    out = await _c(h).activity("2026-05-08", "2026-06-07")
    assert seen == {"s": "2026-05-08", "e": "2026-06-07"}
    assert out["sum_api_requests"] == 10
