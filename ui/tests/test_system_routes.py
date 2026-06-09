"""Tests for /api/cache/stats and /api/proxy-info."""
from __future__ import annotations
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw")
    os.environ["SESSION_SECRET"] = "s"
    os.environ["CONFIG_PATH"] = str(tmp_path / "c.yaml")
    (tmp_path / "c.yaml").write_text("general_settings: {}\n")
    from app.main import create_app
    c = TestClient(create_app())
    c.post("/api/auth/login", json={"password": "pw"})
    return c


def _fake_redis_class(info_data: dict):
    """Return a fake redis.asyncio.Redis class with async ping/info/aclose."""
    instance = MagicMock()
    instance.ping = AsyncMock(return_value=True)
    instance.info = AsyncMock(return_value=info_data)
    instance.aclose = AsyncMock(return_value=None)

    cls = MagicMock(return_value=instance)
    return cls


_GOOD_INFO = {
    "keyspace_hits": 80,
    "keyspace_misses": 20,
    "used_memory": 1048576,
    "used_memory_human": "1.00M",
    "used_memory_peak_human": "1.20M",
    "evicted_keys": 0,
    "connected_clients": 3,
    "uptime_in_seconds": 3600,
    "db0": {"keys": 5, "expires": 0, "avg_ttl": 0},
}


def test_cache_stats_requires_login(tmp_path):
    c = _client(tmp_path)
    c.cookies.clear()
    assert c.get("/api/cache/stats").status_code == 401


def test_cache_stats_connected(tmp_path):
    c = _client(tmp_path)
    fake_cls = _fake_redis_class(_GOOD_INFO)
    with patch("redis.asyncio.Redis", fake_cls):
        d = c.get("/api/cache/stats").json()
    assert d["connected"] is True
    assert d["keyspace_hits"] == 80
    assert d["keyspace_misses"] == 20
    assert abs(d["hit_rate"] - 0.8) < 1e-6
    assert d["db_keys"] == 5
    assert d["used_memory_human"] == "1.00M"
    assert "rtt_ms" in d
    assert d["backend"].endswith(":6379") or ":" in d["backend"]


def test_cache_stats_graceful_on_failure(tmp_path):
    c = _client(tmp_path)
    error_cls = MagicMock(side_effect=ConnectionRefusedError("refused"))
    with patch("redis.asyncio.Redis", error_cls):
        d = c.get("/api/cache/stats").json()
    # Must be 200 with connected:false, not a 5xx
    assert d["connected"] is False
    assert "error" in d
    assert "backend" in d


def test_cache_stats_zero_hits_hit_rate_none(tmp_path):
    c = _client(tmp_path)
    info = {**_GOOD_INFO, "keyspace_hits": 0, "keyspace_misses": 0}
    fake_cls = _fake_redis_class(info)
    with patch("redis.asyncio.Redis", fake_cls):
        d = c.get("/api/cache/stats").json()
    assert d["connected"] is True
    assert d["hit_rate"] is None


def test_proxy_info_requires_login(tmp_path):
    c = _client(tmp_path)
    c.cookies.clear()
    assert c.get("/api/proxy-info").status_code == 401


def test_proxy_info_defaults(tmp_path):
    # Clear any stale proxy env vars to get defaults
    os.environ.pop("LITELLM_PROXY_PORT", None)
    os.environ.pop("LITELLM_PROXY_HOST", None)
    c = _client(tmp_path)
    d = c.get("/api/proxy-info").json()
    assert d["proxy_port"] == "4000"
    assert d["proxy_host"] is None


def test_proxy_info_custom_port(tmp_path):
    os.environ["LITELLM_PROXY_PORT"] = "4100"
    os.environ.pop("LITELLM_PROXY_HOST", None)
    c = _client(tmp_path)
    d = c.get("/api/proxy-info").json()
    assert d["proxy_port"] == "4100"
    assert d["proxy_host"] is None
    # Clean up
    os.environ.pop("LITELLM_PROXY_PORT", None)


def test_proxy_info_custom_host(tmp_path):
    os.environ["LITELLM_PROXY_PORT"] = "4000"
    os.environ["LITELLM_PROXY_HOST"] = "10.0.20.85"
    c = _client(tmp_path)
    d = c.get("/api/proxy-info").json()
    assert d["proxy_host"] == "10.0.20.85"
    # Clean up
    os.environ.pop("LITELLM_PROXY_PORT", None)
    os.environ.pop("LITELLM_PROXY_HOST", None)
