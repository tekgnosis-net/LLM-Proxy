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


def test_preview_requires_login(tmp_path):
    c = _client(tmp_path, FakeMcp()); c.cookies.clear()
    assert c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp"}).status_code == 401


def test_preview_validates_url_and_transport(tmp_path):
    c = _client(tmp_path, FakeMcp())
    assert c.post("/api/mcp/tools/preview", json={"url": "ftp://x"}).status_code == 422
    r = c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp", "transport": "sse"})
    assert r.status_code == 422 and "Apply the server first" in r.json()["detail"]


def test_preview_calls_probe_and_returns_tools(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    seen = {}
    async def fake_probe(url, **kw):
        seen["url"] = url; seen["kw"] = kw
        return [{"name": "a", "description": "d"}]
    import app.routes.mcp_routes as mr
    monkeypatch.setattr(mr, "probe_tools", fake_probe)
    r = c.post("/api/mcp/tools/preview", json={
        "url": "http://x/mcp", "auth_type": "bearer_token", "auth_value": "tok",
        "static_headers": {"X-E": "1"}})
    assert r.status_code == 200 and r.json() == {"tools": [{"name": "a", "description": "d"}]}
    assert seen["kw"]["auth_value"] == "tok" and seen["kw"]["static_headers"] == {"X-E": "1"}


def test_preview_probe_error_maps_422(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    import app.routes.mcp_routes as mr
    from app.mcp_probe import ProbeError
    async def boom(url, **kw): raise ProbeError("server returned 401 — check the auth type/value")
    monkeypatch.setattr(mr, "probe_tools", boom)
    r = c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp"})
    assert r.status_code == 422 and "401" in r.json()["detail"]


def test_preview_blank_auth_uses_stored_secret(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    # _client pins DATABASE_URL="" — the stored-secret path needs a non-empty DSN,
    # so override AFTER client creation and clear the lru_cached settings
    os.environ["DATABASE_URL"] = "fake://test"
    from app.settings import get_settings
    get_settings.cache_clear()
    import app.routes.mcp_routes as mr

    class FakeStore:
        async def applied(self):
            return [{"kind": "mcp_server", "name": "u1",
                     "data": {"server_name": "s", "auth_type": "bearer_token",
                              "url": "http://x/mcp",
                              "auth_value_encrypted": "ENC:tok-stored"}}]
        async def staged(self): return []
    monkeypatch.setattr(mr, "make_preview_store", lambda: FakeStore())

    class FakeFernet:
        def decrypt(self, b): return b[4:]
    monkeypatch.setattr(mr, "_preview_fernet", lambda: FakeFernet())

    seen = {}
    async def fake_probe(url, **kw):
        seen["auth_value"] = kw.get("auth_value")
        return []
    monkeypatch.setattr(mr, "probe_tools", fake_probe)
    r = c.post("/api/mcp/tools/preview", json={
        "url": "http://x/mcp", "auth_type": "bearer_token", "auth_value": "", "server_id": "u1"})
    assert r.status_code == 200 and seen["auth_value"] == "tok-stored"
    os.environ.pop("DATABASE_URL", None)


def test_preview_stored_secret_origin_mismatch(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    os.environ["DATABASE_URL"] = "fake://test"
    from app.settings import get_settings
    get_settings.cache_clear()
    import app.routes.mcp_routes as mr

    class FakeStore:
        async def applied(self):
            return [{"kind": "mcp_server", "name": "u1",
                     "data": {"server_name": "s", "auth_type": "bearer_token",
                              "url": "http://other-host/mcp",
                              "auth_value_encrypted": "ENC:tok-stored"}}]
        async def staged(self): return []
    monkeypatch.setattr(mr, "make_preview_store", lambda: FakeStore())

    class FakeFernet:
        def decrypt(self, b): return b[4:]
    monkeypatch.setattr(mr, "_preview_fernet", lambda: FakeFernet())

    calls = []
    async def fake_probe(url, **kw):
        calls.append(url)
        return []
    monkeypatch.setattr(mr, "probe_tools", fake_probe)
    r = c.post("/api/mcp/tools/preview", json={
        "url": "http://x/mcp", "auth_type": "bearer_token", "auth_value": "", "server_id": "u1"})
    assert r.status_code == 422 and "host differs" in r.json()["detail"]
    assert calls == []
    os.environ.pop("DATABASE_URL", None)


def test_preview_stored_secret_decrypt_failure_422(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    os.environ["DATABASE_URL"] = "fake://test"
    from app.settings import get_settings
    get_settings.cache_clear()
    import app.routes.mcp_routes as mr

    class FakeStore:
        async def applied(self):
            return [{"kind": "mcp_server", "name": "u1",
                     "data": {"server_name": "s", "auth_type": "bearer_token",
                              "url": "http://x/mcp",
                              "auth_value_encrypted": "ENC:tok-stored"}}]
        async def staged(self): return []
    monkeypatch.setattr(mr, "make_preview_store", lambda: FakeStore())

    class FakeFernet:
        def decrypt(self, b): raise ValueError("boom")
    monkeypatch.setattr(mr, "_preview_fernet", lambda: FakeFernet())

    r = c.post("/api/mcp/tools/preview", json={
        "url": "http://x/mcp", "auth_type": "bearer_token", "auth_value": "", "server_id": "u1"})
    assert r.status_code == 422 and "could not be decrypted" in r.json()["detail"]
    assert "boom" not in r.text and "tok-stored" not in r.text
    os.environ.pop("DATABASE_URL", None)


def test_preview_blank_auth_without_stored_secret_422(tmp_path):
    c = _client(tmp_path, FakeMcp())
    r = c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp", "auth_type": "api_key"})
    assert r.status_code == 422 and "auth_value" in r.json()["detail"]
