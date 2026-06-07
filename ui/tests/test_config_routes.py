import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, reloader_ok=True):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    os.environ["DATABASE_URL"] = "postgresql://x"
    (tmp_path / "config.yaml").write_text("router_settings:\n  routing_strategy: least-busy\n")
    from app.main import create_app
    import app.routes.config_routes as cr
    import app.routes.config_v3_routes as crv3

    class FakeReloader:
        async def reload_and_verify(self, expected_models):
            if not reloader_ok:
                from app.reloader import ReloadError
                raise ReloadError("sim")
            return True

    class _FakeFernet:
        def encrypt(self, b): return b"ENC:"+b
        def decrypt(self, b): return b[4:] if b.startswith(b"ENC:") else b

    class _FakeStore:
        def __init__(self):
            self._applied=[]; self._staged=[]
        async def applied(self): return list(self._applied)
        async def staged(self): return list(self._staged)
        async def staged_count(self): return len(self._staged)
        async def stage(self, kind, name, data, *, deleted=False): pass
        async def clear_staged(self, kind=None, name=None): pass
        async def fold(self): self._staged=[]

    cr.make_reloader = lambda: FakeReloader()   # test seam (v2)
    crv3.make_reloader = lambda: FakeReloader()  # test seam (v3 — owns /api/apply + /api/discard)
    crv3.make_config_store = lambda: _FakeStore()
    crv3._fernet = lambda: _FakeFernet()
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
    # /api/apply is now owned by config_v3_routes (v3 routes registered first).
    # v3 apply: commit-at-write, healthy servant → 200 {applied:true, servant:"healthy"}.
    c = _client(tmp_path, reloader_ok=True)
    r = c.post("/api/apply")
    assert r.status_code == 200 and r.json()["applied"] is True and r.json()["servant"] == "healthy"


def test_apply_rollback_409(tmp_path):
    # /api/apply is now owned by config_v3_routes. v3 never rolls back on servant failure;
    # unhealthy servant still returns 200 {applied:true, servant:"unhealthy"} (commit-at-write).
    c = _client(tmp_path, reloader_ok=False)
    r = c.post("/api/apply")
    assert r.status_code == 200 and r.json()["applied"] is True and r.json()["servant"] == "unhealthy"


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
    # /api/discard is now owned by config_v3_routes (v3 routes registered first).
    # v3 discard clears the DB staging table; it does not revert the YAML file (file-revert is v2 only).
    c = _client(tmp_path)
    r = c.post("/api/discard")
    assert r.status_code == 200 and r.json()["pending"] is False


def test_discard_requires_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.post("/api/discard").status_code == 401


def test_discard_no_baseline_is_noop(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/discard")
    assert r.status_code == 200 and "pending" in r.json()
