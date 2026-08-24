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
