import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw"); os.environ["SESSION_SECRET"] = "s"
    os.environ["CONFIG_PATH"] = str(tmp_path / "c.yaml"); (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.usage_routes as ur
    ur.make_spend_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


class FakeSpend:
    async def total_spend(self): return {"spend": 4.23, "max_budget": None}
    async def spend_by_model(self): return [{"model": "gpt-4o", "total_spend": 2.1}]
    async def spend_by_key(self): return [{"key_alias": "ci", "total_spend": 1.0}]
    async def activity(self, s, e): return {"daily_data": [], "sum_api_requests": 0, "sum_total_tokens": 0}


def test_usage_requires_login(tmp_path):
    c = _client(tmp_path, FakeSpend()); c.cookies.clear()
    assert c.get("/api/usage").status_code == 401


def test_usage_combines(tmp_path):
    c = _client(tmp_path, FakeSpend())
    d = c.get("/api/usage").json()
    assert d["total"]["spend"] == 4.23
    assert d["by_model"][0]["model"] == "gpt-4o"
    assert d["by_key"][0]["key_alias"] == "ci"
    assert "activity" in d


def test_usage_resilient_to_partial_failure(tmp_path):
    class Partial(FakeSpend):
        async def spend_by_model(self): raise RuntimeError("boom")
    d = _client(tmp_path, Partial()).get("/api/usage").json()
    assert d["total"]["spend"] == 4.23     # other sections still present
    assert d["by_model"] == []             # failed section degrades to empty
