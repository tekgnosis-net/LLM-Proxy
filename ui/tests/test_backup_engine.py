import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.backup_engine import (local_stamp, parse_dsn, pg_dump_cmd, pg_restore_cmd,
                               build_manifest, verify_manifest, prune_candidates, fingerprints)

TZ = timezone(timedelta(hours=10))


def test_local_stamp_filesystem_safe():
    assert local_stamp(datetime(2026, 8, 24, 3, 0, 0, tzinfo=TZ)) == "2026-08-24T03-00-00+1000"


def test_parse_dsn_and_pg_cmds():
    d = parse_dsn("postgresql://u:p%40ss@postgres:5432/litellm")
    assert d == {"host": "postgres", "port": 5432, "user": "u", "password": "p@ss", "dbname": "litellm"}
    argv, env = pg_dump_cmd("postgresql://u:pw@h:5432/db", "/b/x.dump", ["LiteLLM_SpendLogs"])
    assert argv[0] == "pg_dump" and "-Fc" in argv and "--no-owner" in argv and "--no-privileges" in argv
    assert '--exclude-table=public."LiteLLM_SpendLogs"' in argv and "/b/x.dump" in argv
    assert env["PGPASSWORD"] == "pw" and "pw" not in " ".join(argv)
    # Test exclude_table_data parameter
    argv, env = pg_dump_cmd("postgresql://u:pw@h:5432/db", "/b/x.dump", ["LiteLLM_SpendLogs"], exclude_table_data=["_prisma_migrations"])
    assert '--exclude-table-data=public."_prisma_migrations"' in argv
    # Test that omitting exclude_table_data adds no --exclude-table-data entries
    argv, env = pg_dump_cmd("postgresql://u:pw@h:5432/db", "/b/x.dump", ["LiteLLM_SpendLogs"])
    assert not any(arg.startswith('--exclude-table-data') for arg in argv)
    rargv, renv = pg_restore_cmd("postgresql://u:pw@h:5432/db", "/b/x.dump")
    assert rargv[0] == "pg_restore" and "--data-only" in rargv and "--disable-triggers" in rargv


def test_manifest_roundtrip(tmp_path):
    f = tmp_path / "a.txt"; f.write_text("hello")
    m = build_manifest("config", "2026-08-24T03:00:00+10:00", {"a.txt": f}, item_counts={"model": 3})
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    got, errs = verify_manifest(tmp_path)
    assert errs == [] and got["tier"] == "config" and got["files"]["a.txt"]["bytes"] == 5
    f.write_text("tampered!")
    _, errs2 = verify_manifest(tmp_path)
    assert errs2 and "a.txt" in errs2[0]


def test_prune_candidates_respects_retention_and_zero():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=TZ)
    old = (now - timedelta(days=20)).isoformat()
    new = (now - timedelta(days=2)).isoformat()
    entries = [("config/old", old), ("config/new", new)]
    assert prune_candidates(entries, 14, now) == ["config/old"]
    assert prune_candidates(entries, 0, now) == []


def test_fingerprints_are_short_hashes():
    fp = fingerprints("salt", "fern")
    assert set(fp) == {"salt", "fernet"} and all(len(v) == 12 for v in fp.values())
    assert fingerprints(None, "fern")["salt"] is None


# append to ui/tests/test_backup_engine.py
import gzip, os
import pytest
from app import backup_engine
from app.backup_engine import BackupEngine

pytestmark = pytest.mark.asyncio
TZ10 = timezone(timedelta(hours=10))
NOW = datetime(2026, 8, 24, 3, 0, 0, tzinfo=TZ10)


class FakeRunStore:
    def __init__(self): self.finished = []
    async def start_run(self, tier): return 7
    async def finish_run(self, rid, status, path=None, bytes_=0, error=None, meta=None):
        self.finished.append((rid, status, path, error, meta))


class FakeConfigStore:
    async def applied(self):
        return [{"kind": "model", "name": "id1", "data": {"model_name": "m"}}]


class FakeConn:
    """Answers base_tables + column checks + count + copy_from_query."""
    def __init__(self, tables, rows_by_table=None):
        self._tables = tables
        self._rows = rows_by_table or {}
    async def fetch(self, q, *a):
        if "information_schema.tables" in q:
            return [{"table_name": t} for t in self._tables]
        if "information_schema.columns" in q:
            return [{"column_name": c} for c in
                    {"LiteLLM_SpendLogs": ["startTime", "request_id"],
                     "LiteLLM_DailyTagSpend": ["date", "id"]}.get(a[0], [])]
        return []
    async def fetchval(self, q, *a):
        for t, rows in self._rows.items():
            if f'"{t}"' in q: return len(rows)
        return 0
    async def copy_from_query(self, q, *a, output=None, format=None, header=None, **kw):
        for t, rows in self._rows.items():
            if f'"{t}"' in q:
                data = "col1,col2\n" + "".join(f"{x},{y}\n" for x, y in rows)
                # Match real asyncpg's contract (0.31): output is awaited, not just
                # called. A sync sink would raise TypeError here instead of hiding.
                await output(data.encode())
                return
    async def close(self): pass


def make_engine(tmp_path, tables, rows=None, rc=0):
    calls = []
    async def run_sub(argv, env):
        calls.append((argv, env))
        # simulate pg_dump creating its output file
        for i, tok in enumerate(argv):
            if tok == "-f": Path(argv[i + 1]).write_bytes(b"PGDMP-fake")
        return rc, "" if rc == 0 else "boom"
    cfg = tmp_path / "config.yaml"; cfg.write_text("model_list: []\n")
    conn = FakeConn(tables, rows)
    async def connect(): return conn
    eng = BackupEngine("postgresql://u:pw@h:5432/db", str(tmp_path / "backups"),
                       FakeRunStore(), FakeConfigStore(), str(cfg),
                       fernet_secret="fs", salt_key="sk",
                       run_subprocess=run_sub, connect=connect, now=lambda: NOW)
    return eng, calls


async def test_run_config_creates_bundle_and_manifest(tmp_path):
    eng, calls = make_engine(tmp_path, ["LiteLLM_SpendLogs", "LiteLLM_TeamTable", "ui_config_applied"])
    out = await eng.run_config()
    assert out["ok"] is True
    d = Path(tmp_path, "backups", "config", "2026-08-24T03-00-00+1000")
    assert (d / "litellm-config.dump").exists() and (d / "ui_config.json").exists() \
        and (d / "config.yaml").exists() and (d / "manifest.json").exists()
    assert oct(d.stat().st_mode & 0o777) == "0o700"
    assert oct(Path(tmp_path, "backups").stat().st_mode & 0o777) == "0o700"          # backup root
    assert oct(Path(tmp_path, "backups", "config").stat().st_mode & 0o777) == "0o700"  # tier dir
    argv, env = calls[0]
    assert '--exclude-table=public."LiteLLM_SpendLogs"' in argv        # usage excluded
    assert '--exclude-table=public."LiteLLM_HealthCheckTable"' in argv  # transient excluded
    assert '--exclude-table-data=public."_prisma_migrations"' in argv  # data excluded
    m = json.loads((d / "manifest.json").read_text())
    assert m["tier"] == "config" and m["fingerprints"]["fernet"] and m["item_counts"] == {"model": 1}
    assert m["excluded_data"] == ["_prisma_migrations"]


async def test_run_config_failure_cleans_partial_dir(tmp_path):
    eng, _ = make_engine(tmp_path, ["LiteLLM_TeamTable"], rc=1)
    out = await eng.run_config()
    assert out["ok"] is False and "boom" in out["error"]
    assert not any(Path(tmp_path, "backups", "config").glob("*"))


async def test_run_logs_exports_watermarked_csv_and_records_watermark(tmp_path):
    rows = {"LiteLLM_SpendLogs": [(1, "a"), (2, "b")], "LiteLLM_DailyTagSpend": [(3, "c")]}
    eng, _ = make_engine(tmp_path, list(rows), rows)
    out = await eng.run_logs()
    assert out["ok"] is True
    d = next(Path(tmp_path, "backups", "logs").iterdir())
    with gzip.open(d / "LiteLLM_SpendLogs.csv.gz", "rt") as f:
        assert f.readline().strip() == "col1,col2"
    m = json.loads((d / "manifest.json").read_text())
    assert m["tables"]["LiteLLM_SpendLogs"]["rows"] == 2
    assert m["tables"]["LiteLLM_SpendLogs"]["to"]           # watermark recorded


async def test_run_logs_empty_slice_writes_manifest_only(tmp_path):
    eng, _ = make_engine(tmp_path, ["LiteLLM_SpendLogs"], {"LiteLLM_SpendLogs": []})
    out = await eng.run_logs()
    assert out["ok"] is True
    d = next(Path(tmp_path, "backups", "logs").iterdir())
    assert (d / "manifest.json").exists() and not (d / "LiteLLM_SpendLogs.csv.gz").exists()


async def test_snapshot_write_and_prune_to_50(tmp_path):
    eng, _ = make_engine(tmp_path, [])
    for i in range(55):
        eng._now = lambda i=i: NOW + timedelta(minutes=i)   # distinct stamps
        eng.write_snapshot([{"kind": "model", "name": str(i), "data": {}}])
    snaps = list(Path(tmp_path, "backups", "snapshots").glob("*.json"))
    assert len(snaps) == 50


async def test_backup_path_rejects_traversal(tmp_path):
    eng, _ = make_engine(tmp_path, [])
    with pytest.raises(ValueError): eng.backup_path("config/../../etc")
    with pytest.raises(ValueError): eng.backup_path("nope/x")
    p = eng.backup_path("snapshots/x.json")
    assert str(p).startswith(str(tmp_path / "backups"))


async def test_run_config_rejects_when_already_running(tmp_path):
    eng, _ = make_engine(tmp_path, [])
    async with backup_engine._LOCKS["config"]:
        out = await eng.run_config()
    assert out == {"ok": False, "error": "already running"}


async def test_run_logs_rejects_when_already_running(tmp_path):
    eng, _ = make_engine(tmp_path, [])
    async with backup_engine._LOCKS["logs"]:
        out = await eng.run_logs()
    assert out == {"ok": False, "error": "already running"}
