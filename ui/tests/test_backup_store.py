import json, os, pytest
from pathlib import Path
from app.backup_store import DEFAULTS, validate_tier_settings, write_mirror, read_mirror


def test_defaults():
    assert DEFAULTS["config"] == {"enabled": True, "frequency": {"kind": "daily"},
                                  "time": "03:00", "retention_days": 14}
    assert DEFAULTS["logs"] == {"enabled": False, "frequency": {"kind": "daily"},
                                "time": "03:30", "retention_days": 0}


def test_validate_normalizes_and_rejects():
    v = validate_tier_settings({"enabled": True, "frequency": {"kind": "weekly", "weekday": 6},
                                "time": "9:5", "retention_days": 7})
    assert v["time"] == "09:05"
    with pytest.raises(ValueError): validate_tier_settings({"frequency": {"kind": "hourly"}, "time": "03:00"})
    with pytest.raises(ValueError): validate_tier_settings({"frequency": {"kind": "daily"}, "time": "25:00"})
    with pytest.raises(ValueError): validate_tier_settings({"frequency": {"kind": "every_n_days"}, "time": "03:00"})
    with pytest.raises(ValueError): validate_tier_settings({"frequency": {"kind": "weekly", "weekday": 9}, "time": "03:00"})
    with pytest.raises(ValueError): validate_tier_settings({"frequency": {"kind": "daily"}, "time": "03:00", "retention_days": -1})


def test_mirror_roundtrip_0600(tmp_path):
    s = {"config": DEFAULTS["config"], "logs": DEFAULTS["logs"]}
    write_mirror(tmp_path, s)
    p = tmp_path / "settings.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert read_mirror(tmp_path) == s
    assert read_mirror(tmp_path / "nope") is None


import asyncpg
from app.backup_store import BackupStore

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://test:testpass@localhost:15432/testdb")
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def bstore():
    try:
        conn = await asyncpg.connect(TEST_DSN)
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"no test Postgres at {TEST_DSN} ({e})")
    await conn.execute("DROP TABLE IF EXISTS ui_backup_runs")
    await conn.execute("DROP TABLE IF EXISTS ui_settings")
    await conn.close()
    yield BackupStore(TEST_DSN)
    conn = await asyncpg.connect(TEST_DSN)
    await conn.execute("DROP TABLE IF EXISTS ui_backup_runs")
    await conn.execute("DROP TABLE IF EXISTS ui_settings")
    await conn.close()


async def test_settings_roundtrip_and_defaults(bstore):
    assert await bstore.settings_present() is False
    s = await bstore.get_settings()
    assert s == {"config": DEFAULTS["config"], "logs": DEFAULTS["logs"]}
    await bstore.save_settings("logs", {"enabled": True, "frequency": {"kind": "every_n_days", "n": 2},
                                        "time": "01:30", "retention_days": 0})
    assert await bstore.settings_present() is True
    assert (await bstore.get_settings())["logs"]["frequency"] == {"kind": "every_n_days", "n": 2}


async def test_run_lifecycle(bstore):
    rid = await bstore.start_run("config")
    await bstore.finish_run(rid, "ok", path="config/x", bytes_=123, meta={"tables": 5})
    last = await bstore.last_run("config")
    assert last["status"] == "ok" and last["bytes"] == 123 and last["meta"] == {"tables": 5}
    assert (await bstore.runs("config"))[0]["id"] == rid
    assert await bstore.last_run("logs") is None
