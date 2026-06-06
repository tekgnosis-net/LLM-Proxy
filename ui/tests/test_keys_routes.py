import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.keys_routes as kr
    kr.make_keys_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


class FakeKeys:
    def __init__(self): self.deleted = None
    async def list_keys(self): return [{"token": "h1", "key_alias": "ci", "spend": 0.5, "max_budget": 10, "models": []}]
    async def generate_key(self, payload): return {"key": "sk-NEW", "token": "h2", **payload}
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
