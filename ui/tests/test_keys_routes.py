import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake, clear_db_url=True):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    if clear_db_url:
        os.environ.pop("DATABASE_URL", None)  # clear any previous DB URL
    (tmp_path / "config.yaml").write_text("model_list: []\n")
    # Clear settings cache so new environ is picked up
    from app.settings import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    import app.routes.keys_routes as kr
    kr.make_keys_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


class FakeKeys:
    def __init__(self): self.deleted = None
    async def list_keys(self): return [{"token": "h1", "key_alias": "ci", "spend": 0.5, "max_budget": 10, "models": []}]
    async def generate_key(self, payload): return {"key": "sk-NEW", "token": "h2", **payload}
    async def update_key(self, payload): return {"updated": True, **payload}
    async def delete_keys(self, tokens): self.deleted = tokens; return {"deleted_keys": tokens}


def test_keys_requires_login(tmp_path):
    c = _client(tmp_path, FakeKeys()); c.cookies.clear()
    assert c.get("/api/keys").status_code == 401


def test_list_keys(tmp_path):
    c = _client(tmp_path, FakeKeys())
    r = c.get("/api/keys"); assert r.status_code == 200
    assert r.json()[0]["key_alias"] == "ci"
    assert "key" not in r.json()[0]  # list never returns plaintext


def test_generate_key_returns_plaintext(tmp_path):
    c = _client(tmp_path, FakeKeys())
    r = c.post("/api/keys", json={"key_alias": "ci", "max_budget": 10})
    assert r.status_code == 200 and r.json()["key"] == "sk-NEW"


def test_delete_key(tmp_path):
    fake = FakeKeys(); c = _client(tmp_path, fake)
    r = c.post("/api/keys/delete", json={"tokens": ["h1"]})
    assert r.status_code == 200 and fake.deleted == ["h1"]


class FakeConfigStore:
    def __init__(self, groups):
        self._items = [{"kind": "model", "name": f"id{i}", "data": {"model_name": g}}
                       for i, g in enumerate(groups)]
    async def applied(self): return list(self._items)
    async def staged(self): return []


def _client_v(tmp_path, fake, groups):
    os.environ["DATABASE_URL"] = "fake://test"  # enable validation
    c = _client(tmp_path, fake, clear_db_url=False)
    import app.routes.keys_routes as kr
    kr.make_config_store = lambda: FakeConfigStore(groups)
    return c


def test_create_key_rejects_dead_allowed_model(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["deadgroup"]})
    assert r.status_code == 422 and "deadgroup" in r.json()["detail"]


def test_create_key_allows_alias_name_in_models(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["gpt-oss-20b-1x", "myalias"],
                                  "aliases": {"myalias": "gpt-oss-20b-1x"}})
    assert r.status_code == 200                       # alias name legitimately in models


def test_update_key_rejects_dead_alias_target(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys/update", json={"key": "h1", "aliases": {"gpt-4": "deadgroup"}})
    assert r.status_code == 422 and "deadgroup" in r.json()["detail"]


def test_create_key_clean_passes(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["gpt-oss-20b-1x"]})
    assert r.status_code == 200


def test_create_key_allows_special_model_tokens(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["all-proxy-models"]})
    assert r.status_code == 200


def test_create_key_malformed_models_entry_no_500(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["gpt-oss-20b-1x", ["nested"]]})
    assert r.status_code == 200      # malformed entry skipped, not a 500


class FakeConfigStoreMcp(FakeConfigStore):
    def __init__(self, groups, mcp):
        super().__init__(groups)
        self._items += [{"kind": "mcp_server", "name": name, "data": {"server_name": sn}}
                        for name, sn in mcp]


def _client_mcp(tmp_path, fake, groups, mcp):
    os.environ["DATABASE_URL"] = "fake://test"  # enable validation
    c = _client(tmp_path, fake, clear_db_url=False)
    import app.routes.keys_routes as kr
    kr.make_config_store = lambda: FakeConfigStoreMcp(groups, mcp)
    return c


def test_create_key_rejects_unknown_mcp_grant(tmp_path):
    c = _client_mcp(tmp_path, FakeKeys(), groups=["g1"], mcp=[("u1", "deepwiki")])
    r = c.post("/api/keys", json={"key_alias": "x", "object_permission": {"mcp_servers": ["nope"]}})
    assert r.status_code == 422
    assert "unknown MCP server" in r.json()["detail"] and "nope" in r.json()["detail"]


def test_create_key_accepts_mcp_grant_by_uuid_or_name(tmp_path):
    c = _client_mcp(tmp_path, FakeKeys(), groups=["g1"], mcp=[("u1", "deepwiki")])
    r = c.post("/api/keys", json={"key_alias": "x",
                                  "object_permission": {"mcp_servers": ["u1", "deepwiki"]}})
    assert r.status_code == 200
    # FakeKeys echoes the payload — grants forwarded verbatim, untouched
    assert r.json()["object_permission"] == {"mcp_servers": ["u1", "deepwiki"]}


def test_update_key_rejects_unknown_mcp_grant(tmp_path):
    c = _client_mcp(tmp_path, FakeKeys(), groups=["g1"], mcp=[("u1", "deepwiki")])
    r = c.post("/api/keys/update", json={"key": "h1", "object_permission": {"mcp_servers": ["dead"]}})
    assert r.status_code == 422 and "dead" in r.json()["detail"]
