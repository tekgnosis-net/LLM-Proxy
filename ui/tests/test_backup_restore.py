import json
import pytest
from cryptography.fernet import Fernet
from app.credentials_store import fernet_from_secret
from app.backup_restore import rollback_preview, check_decryptable, parse_export


def _it(kind, name, data): return {"kind": kind, "name": name, "data": data}


def test_rollback_preview_diff():
    cur = [_it("model", "a", {"x": 1}), _it("model", "b", {"x": 1}), _it("router_setting", "timeout", 300)]
    new = [_it("model", "a", {"x": 2}), _it("model", "c", {"x": 1}), _it("router_setting", "timeout", 300)]
    d = rollback_preview(cur, new)
    assert d["added"] == [{"kind": "model", "name": "c"}]
    assert d["removed"] == [{"kind": "model", "name": "b"}]
    assert d["changed"] == [{"kind": "model", "name": "a"}]
    assert d["restart_kinds_changed"] is False
    d2 = rollback_preview(cur, cur[:2] + [_it("router_setting", "timeout", 600)])
    assert d2["restart_kinds_changed"] is True


def test_check_decryptable_flags_wrong_secret():
    f_good, f_bad = fernet_from_secret("good"), fernet_from_secret("bad")
    enc = f_bad.encrypt(b"k").decode()
    items = [_it("credential", "DI", {"provider": "x", "value_encrypted": enc}),
             _it("mcp_server", "m1", {"auth_value_encrypted": enc}),
             _it("model", "a", {})]
    assert check_decryptable(items, f_good) == ["credential/DI", "mcp_server/m1"]
    assert check_decryptable(items, f_bad) == []


def test_parse_export_validates():
    items = [_it("model", "a", {})]
    assert parse_export(json.dumps({"version": 1, "items": items})) == items
    with pytest.raises(ValueError): parse_export("not json")
    with pytest.raises(ValueError): parse_export(json.dumps({"version": 1, "items": "nope"}))
    with pytest.raises(ValueError): parse_export(json.dumps({"version": 1, "items": [{"kind": "model"}]}))


from datetime import datetime
from pathlib import Path
from app.backup_restore import truncate_statement, check_fingerprints, full_recovery
from app.backup_engine import build_manifest, fingerprints as make_fps  # reuse helpers

pytestmark = pytest.mark.asyncio


def test_truncate_statement_skips_prisma_and_quotes():
    sql = truncate_statement(["LiteLLM_TeamTable", "_prisma_migrations", "ui_config_applied"])
    assert sql == 'TRUNCATE "LiteLLM_TeamTable", "ui_config_applied"'


def test_check_fingerprints():
    m = {"fingerprints": make_fps("salt", "fern")}
    assert check_fingerprints(m, "salt", "fern") == []
    assert check_fingerprints(m, "other", "fern") == ["salt"]
    assert check_fingerprints({}, "salt", "fern") == []          # old manifest: no check possible


def test_check_fingerprints_skips_when_have_side_missing():
    # Production: the UI never holds the LiteLLM salt key, so salt_key is None —
    # comparison must be skipped rather than treated as a mismatch.
    m = {"fingerprints": make_fps("salt", "fern")}
    assert check_fingerprints(m, None, "fern") == []


class RecConn:
    def __init__(self): self.sql = []
    async def fetch(self, q, *a):
        return [{"table_name": t} for t in ["LiteLLM_TeamTable", "ui_config_applied",
                                            "_prisma_migrations", "LiteLLM_SpendLogs"]]
    async def execute(self, q, *a): self.sql.append(q)
    async def close(self): pass


class FakeReloader:
    def __init__(self): self.calls = []
    async def stop(self): self.calls.append("stop")
    async def start(self): self.calls.append("start")
    async def verify(self, expected): self.calls.append("verify"); return True


def _make_backup(tmp_path):
    d = tmp_path / "config" / "stamp"; d.mkdir(parents=True)
    (d / "litellm-config.dump").write_bytes(b"PGDMP")
    (d / "ui_config.json").write_text('{"version":1,"items":[]}')
    (d / "config.yaml").write_text("model_list: []\n")
    files = {f.name: f for f in d.iterdir()}
    m = build_manifest("config", datetime.now().astimezone().isoformat(), files,
                       tables=["LiteLLM_TeamTable", "ui_config_applied", "_prisma_migrations",
                               "LiteLLM_GoneTable"],
                       fingerprints=make_fps("salt", "fern"))
    (d / "manifest.json").write_text(json.dumps(m))
    return d


async def test_full_recovery_happy_path(tmp_path):
    d = _make_backup(tmp_path)
    conn = RecConn(); rel = FakeReloader(); calls = []
    async def run_sub(argv, env): calls.append(argv); return 0, ""
    async def connect(): return conn
    cfg = tmp_path / "live.yaml"; cfg.write_text("old: true\n")
    out = await full_recovery(d, dsn="postgresql://u:pw@h:5432/db", reloader=rel,
                              config_path=str(cfg), connect=connect, run_subprocess=run_sub,
                              salt_key="salt", fernet_secret="fern")
    assert out["ok"] is True
    assert rel.calls == ["stop", "start", "verify"]
    # truncated only manifest∩live config tables, never _prisma_migrations or usage:
    assert conn.sql == ['TRUNCATE "LiteLLM_TeamTable", "ui_config_applied"']
    assert calls and calls[0][0] == "pg_restore"
    assert cfg.read_text() == "model_list: []\n"
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps["truncate"] == "ok" and steps["ready"] == "ok"


async def test_full_recovery_fingerprint_mismatch_refuses_before_stop(tmp_path):
    d = _make_backup(tmp_path)
    rel = FakeReloader()
    async def connect(): raise AssertionError("must not connect")
    async def run_sub(argv, env): raise AssertionError("must not run")
    out = await full_recovery(d, dsn="x", reloader=rel, config_path="x",
                              connect=connect, run_subprocess=run_sub,
                              salt_key="WRONG", fernet_secret="fern")
    assert out["ok"] is False and rel.calls == []


async def test_full_recovery_restore_failure_still_starts_litellm(tmp_path):
    d = _make_backup(tmp_path)
    conn = RecConn(); rel = FakeReloader()
    async def run_sub(argv, env): return 1, "restore exploded"
    async def connect(): return conn
    cfg = tmp_path / "live.yaml"; cfg.write_text("old: true\n")
    out = await full_recovery(d, dsn="postgresql://u:pw@h:5432/db", reloader=rel,
                              config_path=str(cfg), connect=connect, run_subprocess=run_sub,
                              salt_key="salt", fernet_secret="fern")
    assert out["ok"] is False
    assert "start" in rel.calls                       # proxy brought back regardless
    assert any(s["step"] == "pg_restore" and s["status"] == "error" for s in out["steps"])


async def test_full_recovery_stop_failure_still_attempts_start(tmp_path):
    d = _make_backup(tmp_path)
    conn = RecConn()

    class FailStopReloader(FakeReloader):
        async def stop(self):
            self.calls.append("stop")
            raise RuntimeError("docker stop failed")

    rel = FailStopReloader()
    async def connect(): return conn
    async def run_sub(argv, env): raise AssertionError("must not run: stop already failed")
    cfg = tmp_path / "live.yaml"; cfg.write_text("old: true\n")
    out = await full_recovery(d, dsn="postgresql://u:pw@h:5432/db", reloader=rel,
                              config_path=str(cfg), connect=connect, run_subprocess=run_sub,
                              salt_key="salt", fernet_secret="fern")
    assert out["ok"] is False
    assert rel.calls == ["stop", "start"]              # best-effort start even though stop failed
    assert conn.sql == []                              # never truncated
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps["stop"] == "error" and steps["start"] == "ok"


async def test_full_recovery_connect_failure_never_stops(tmp_path):
    d = _make_backup(tmp_path)
    rel = FakeReloader()
    async def connect(): raise RuntimeError("db unreachable")
    async def run_sub(argv, env): raise AssertionError("must not run")
    out = await full_recovery(d, dsn="x", reloader=rel, config_path="x",
                              connect=connect, run_subprocess=run_sub,
                              salt_key="salt", fernet_secret="fern")
    assert out["ok"] is False and rel.calls == []       # connect failed before any stop
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps["truncate"] == "error"


class NoMatchConn(RecConn):
    async def fetch(self, q, *a):
        return [{"table_name": t} for t in ["LiteLLM_SomeUnrelatedTable"]]


async def test_full_recovery_no_live_match_never_stops(tmp_path):
    d = _make_backup(tmp_path)
    conn = NoMatchConn(); rel = FakeReloader()
    async def connect(): return conn
    async def run_sub(argv, env): raise AssertionError("must not run")
    out = await full_recovery(d, dsn="x", reloader=rel, config_path="x",
                              connect=connect, run_subprocess=run_sub,
                              salt_key="salt", fernet_secret="fern")
    assert out["ok"] is False and rel.calls == []       # no overlap with live schema: never stopped
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps["truncate"] == "error"
