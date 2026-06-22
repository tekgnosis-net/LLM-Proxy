from app.auth import hash_password, verify_password


def test_hash_then_verify_roundtrip():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h) is True


def test_wrong_password_fails():
    h = hash_password("s3cret")
    assert verify_password("nope", h) is False


def test_empty_hash_always_fails():
    assert verify_password("anything", "") is False


import os
from fastapi.testclient import TestClient


def _client(tmp_path):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("letmein")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("general_settings: {}\n")
    from app.main import create_app
    return TestClient(create_app())


def test_health_requires_login(tmp_path):
    c = _client(tmp_path)
    # /api/config/state requires auth (v3 route); no session → 401
    assert c.get("/api/config/state").status_code == 401


def test_login_then_access(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": "letmein"}).status_code == 200
    # After login, auth passes; no DATABASE_URL in test env → 503 (not 401)
    assert c.get("/api/config/state").status_code != 401


def _client_cookie_secure(tmp_path, monkeypatch, value):
    """Build the app with SESSION_COOKIE_SECURE set to `value`. Uses monkeypatch
    (auto-restores env) + cache_clear before/after so the cached Settings don't
    leak a Secure-cookie config into later tests (an http TestClient would then
    drop the Secure cookie and unrelated auth tests would fail)."""
    from app.settings import get_settings
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("letmein"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("general_settings: {}\n")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", value)
    get_settings.cache_clear()
    from app.main import create_app
    return TestClient(create_app())


def test_session_cookie_secure_flag_set_when_enabled(tmp_path, monkeypatch):
    c = _client_cookie_secure(tmp_path, monkeypatch, "true")
    r = c.post("/api/auth/login", json={"password": "letmein"})
    assert r.status_code == 200
    assert "secure" in r.headers.get("set-cookie", "").lower()
    from app.settings import get_settings
    get_settings.cache_clear()


def test_session_cookie_not_secure_by_default(tmp_path, monkeypatch):
    c = _client_cookie_secure(tmp_path, monkeypatch, "false")
    r = c.post("/api/auth/login", json={"password": "letmein"})
    assert r.status_code == 200
    sc = r.headers.get("set-cookie", "").lower()
    assert "session=" in sc and "secure" not in sc
    from app.settings import get_settings
    get_settings.cache_clear()
