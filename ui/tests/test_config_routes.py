import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, reloader_ok=True):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("router_settings:\n  routing_strategy: least-busy\n")
    from app.main import create_app
    import app.routes.config_routes as cr

    class FakeReloader:
        async def reload_and_verify(self, expected_models):
            if not reloader_ok:
                from app.reloader import ReloadError
                raise ReloadError("sim")
            return True
    cr.make_reloader = lambda: FakeReloader()   # test seam
    c = TestClient(create_app())
    c.post("/api/auth/login", json={"password": "pw"})
    return c


def test_put_config_requires_login(tmp_path):
    c = _client(tmp_path)
    c.cookies.clear()
    assert c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"}}).status_code == 401


def test_put_config_applies(tmp_path):
    c = _client(tmp_path, reloader_ok=True)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"},
                                   "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini"}}]})
    assert r.status_code == 200
    assert c.get("/api/config").json()["router_settings"]["routing_strategy"] == "simple-shuffle"


def test_put_config_invalid_returns_422(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "lowest-cost"}})
    assert r.status_code == 422


def test_put_config_rollback_returns_409(tmp_path):
    c = _client(tmp_path, reloader_ok=False)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"}})
    assert r.status_code == 409
    assert c.get("/api/config").json()["router_settings"]["routing_strategy"] == "least-busy"
