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
    assert c.get("/api/config").status_code == 401


def test_login_then_access(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": "letmein"}).status_code == 200
    assert c.get("/api/config").status_code == 200
