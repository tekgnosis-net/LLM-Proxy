import os
from fastapi.testclient import TestClient
from app.auth import hash_password


class FakeMcp:
    def __init__(self):
        self.health_called_with = None
    async def list_servers(self):
        return [{"server_id": "u1", "server_name": "deepwiki", "status": "healthy",
                 "last_health_check": "2026-08-13T00:00:00", "health_check_error": None,
                 "url": "x", "credentials": None}]
    async def health(self, ids):
        self.health_called_with = ids
        return {"u1": {"status": "healthy"}}
    async def list_tools(self, server_id):
        return {"tools": [{"name": "read_wiki_structure", "description": "d"}]}


def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path / "c.yaml"), DATABASE_URL="")
    (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.mcp_routes as mr
    mr.make_mcp_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


def test_health_requires_login(tmp_path):
    c = _client(tmp_path, FakeMcp()); c.cookies.clear()
    assert c.get("/api/mcp/health").status_code == 401


def test_health_lists_persisted_status(tmp_path):
    d = _client(tmp_path, FakeMcp()).get("/api/mcp/health").json()
    assert d["servers"][0]["server_id"] == "u1" and d["servers"][0]["status"] == "healthy"
    assert "probe" not in d
    assert "credentials" not in d["servers"][0] and "url" not in d["servers"][0]


def test_health_probe_calls_litellm(tmp_path):
    fake = FakeMcp()
    d = _client(tmp_path, fake).get("/api/mcp/health?probe=1&server_ids=u1").json()
    assert fake.health_called_with == ["u1"] and d["probe"] == {"u1": {"status": "healthy"}}


def test_tools_passthrough(tmp_path):
    d = _client(tmp_path, FakeMcp()).get("/api/mcp/tools?server_id=u1").json()
    assert d["tools"][0]["name"] == "read_wiki_structure"


def test_tools_requires_server_id(tmp_path):
    assert _client(tmp_path, FakeMcp()).get("/api/mcp/tools").status_code == 422


def test_usage_no_dsn_returns_empty(tmp_path):
    d = _client(tmp_path, FakeMcp()).get("/api/mcp/usage").json()
    assert d == {"rows": []}
