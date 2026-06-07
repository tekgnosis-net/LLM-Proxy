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


def test_put_config_saves_without_apply(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"}, "model_list": []})
    assert r.status_code == 200 and r.json()["pending"] is True
    assert c.get("/api/apply/status").json()["pending"] is True


def test_put_config_invalid_422(tmp_path):
    c = _client(tmp_path)
    assert c.put("/api/config", json={"router_settings": {"routing_strategy": "lowest-cost"}}).status_code == 422


def test_apply_ok(tmp_path):
    c = _client(tmp_path, reloader_ok=True)
    c.put("/api/config", json={"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    assert c.post("/api/apply").status_code == 200
    assert c.get("/api/apply/status").json()["pending"] is False


def test_apply_rollback_409(tmp_path):
    c = _client(tmp_path, reloader_ok=False)
    c.put("/api/config", json={"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    assert c.post("/api/apply").status_code == 409


def test_apply_requires_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.post("/api/apply").status_code == 401


def test_export_returns_yaml_attachment(tmp_path):
    c = _client(tmp_path)  # reuse the helper (logged in)
    r = c.get("/api/config/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "routing_strategy" in r.text or "model_list" in r.text


def test_cache_info(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/cache/info")
    assert r.status_code == 200
    d = r.json()
    assert "enabled" in d and d["host"] and d["port"]


def test_cache_info_requires_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.get("/api/cache/info").status_code == 401


def test_get_and_export_redact_credential_values(tmp_path):
    c = _client(tmp_path)
    open(os.environ["CONFIG_PATH"], "w").write(
        "model_list: []\n"
        "credential_list:\n"
        "- credential_name: openai\n"
        "  credential_values:\n"
        "    api_key: sk-LEAKME\n"
        "  credential_info: {}\n")
    r = c.get("/api/config")
    assert "sk-LEAKME" not in r.text
    assert r.json()["credential_list"][0]["credential_values"]["api_key"] == "***"
    e = c.get("/api/config/export")
    assert "sk-LEAKME" not in e.text


def test_discard_clears_pending_and_reverts(tmp_path):
    import os
    from app.config_store import load_config
    c = _client(tmp_path)
    c.get("/api/apply/status")  # seeds .applied.yaml from the current config (least-busy)
    c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"}, "model_list": []})
    assert c.get("/api/apply/status").json()["pending"] is True
    r = c.post("/api/discard")
    assert r.status_code == 200 and r.json()["pending"] is False
    assert load_config(os.environ["CONFIG_PATH"]).router_settings.routing_strategy == "least-busy"  # reverted


def test_discard_requires_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.post("/api/discard").status_code == 401


def test_discard_no_baseline_is_noop(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/discard")
    assert r.status_code == 200 and "pending" in r.json()
