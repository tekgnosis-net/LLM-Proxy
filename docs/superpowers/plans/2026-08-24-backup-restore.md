# Backup, Restore & Request Logging (v3.30) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scheduled two-tier backups (config via pg_dump, logs via incremental CSV) with one-click rollback / full recovery / logs-merge restore, the empty-master guard, and full-fidelity request/response logging reviewable in the Activity feed.

**Architecture:** New `backup_*` modules in the FastAPI UI backend own tables classification, settings+run bookkeeping, the engine (pg_dump/CSV+manifests+prune), restores, and APScheduler wiring; a new `/api/backup/*` router and a Settings → Backup & Restore tab expose them. The guard and per-Apply snapshot hook live in `config_v3_routes`. Request bodies surface through the existing `/api/usage/tx/{id}` detail.

**Tech Stack:** Python 3.12 / FastAPI / asyncpg / APScheduler 3.x / pg_dump+pg_restore 16 (PGDG), Svelte 5 (runes) frontend, pytest with factory-monkeypatch fakes.

**Spec:** `docs/superpowers/specs/2026-08-24-backup-restore-design.md`

## Global Constraints

- Work on branch `v3.30-backup-restore` (create from `main` at execution start). Commit per task, conventional commits. NEVER add Claude attribution; end every commit message with the trailer line `Claude-Session: https://claude.ai/code/session_011o3rL25n2rCTBGrXP5uPYE`.
- Backend tests: `cd ui && python -m pytest tests/ -q` (DB-backed tests skip without `TEST_DATABASE_URL`; that skip is acceptable). Frontend check: `cd ui/frontend && npm run build`.
- All new routes require `dependencies=[Depends(login_required)]`.
- Backup files `0600`, directories `0700`. Secrets never appear in manifests, logs, or API responses — only sha256[:12] fingerprints.
- Backup id grammar (spec §9): `^(config|logs|snapshots)/[A-Za-z0-9+._-]+(/[A-Za-z0-9._-]+)?$`, resolved strictly under `BACKUP_DIR`.
- Defaults (spec §5): config tier `{enabled: true, daily, "03:00", retention_days: 14}`; logs tier `{enabled: false, daily, "03:30", retention_days: 0}` (0 = keep forever). Snapshots keep newest **50**.
- Watermark guard 60 s; `Daily*` rolling window **3** days (spec §2). Truncation env `MAX_STRING_LENGTH_PROMPT_IN_DB: "10000000"`.
- Confirmation strings: `ROLLBACK`, `RECOVER`, `MERGE` (spec §6).
- `_prisma_migrations` is dumped but NEVER truncated or restored.

---

### Task 1: Table classification (`backup_tables.py`)

**Files:**
- Create: `ui/app/backup_tables.py`
- Test: `ui/tests/test_backup_tables.py`

**Interfaces:**
- Produces: `classify(tables: list[str]) -> dict` with keys `config`, `usage`, `transient` (sorted lists); `async base_tables(conn) -> list[str]`; constants `USAGE_EXACT: set[str]`, `USAGE_PREFIXES: tuple[str, ...]`, `TRANSIENT: set[str]`, `NEVER_RESTORE: set[str]`, `WATERMARK_COLUMNS: dict[str, str]`, `ROLLING_DATE_COLUMN = "date"`, `ROLLING_WINDOW_DAYS = 3`, `WATERMARK_GUARD_S = 60`.

- [ ] **Step 1: Write the failing test**

```python
# ui/tests/test_backup_tables.py
from app.backup_tables import classify, USAGE_EXACT, NEVER_RESTORE, WATERMARK_COLUMNS


def test_classify_splits_usage_by_exact_and_prefix():
    tables = ["LiteLLM_SpendLogs", "LiteLLM_DailyTagSpend", "LiteLLM_DailyToolSpend",
              "LiteLLM_VerificationToken", "ui_config_applied", "_prisma_migrations",
              "LiteLLM_HealthCheckTable", "LiteLLM_ErrorLogs", "LiteLLM_AuditLog"]
    c = classify(tables)
    assert c["usage"] == sorted(["LiteLLM_SpendLogs", "LiteLLM_DailyTagSpend",
                                 "LiteLLM_DailyToolSpend", "LiteLLM_ErrorLogs", "LiteLLM_AuditLog"])
    assert c["transient"] == ["LiteLLM_HealthCheckTable"]
    assert "_prisma_migrations" in c["config"] and "ui_config_applied" in c["config"]
    assert "LiteLLM_SpendLogs" not in c["config"]


def test_new_daily_table_is_usage_without_code_change():
    c = classify(["LiteLLM_DailyFutureThingSpend", "LiteLLM_TeamTable"])
    assert c["usage"] == ["LiteLLM_DailyFutureThingSpend"]


def test_constants_shape():
    assert "LiteLLM_SpendLogs" in USAGE_EXACT
    assert NEVER_RESTORE == {"_prisma_migrations"}
    assert WATERMARK_COLUMNS["LiteLLM_SpendLogs"] == "startTime"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && python -m pytest tests/test_backup_tables.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.backup_tables'`

- [ ] **Step 3: Write the implementation**

```python
# ui/app/backup_tables.py
"""Single source of truth for which tables belong to which backup tier (spec §2)."""
from __future__ import annotations

USAGE_EXACT = {"LiteLLM_SpendLogs", "LiteLLM_SpendLogToolIndex",
               "LiteLLM_SpendLogGuardrailIndex", "LiteLLM_ErrorLogs", "LiteLLM_AuditLog"}
USAGE_PREFIXES = ("LiteLLM_Daily",)
TRANSIENT = {"LiteLLM_HealthCheckTable"}          # in neither tier
NEVER_RESTORE = {"_prisma_migrations"}            # dumped for provenance, never truncated/restored

# Logs-tier export strategies (spec §2). A table whose column is missing live is
# skipped with a manifest warning — never a crash (the engine checks columns).
WATERMARK_COLUMNS = {"LiteLLM_SpendLogs": "startTime",
                     "LiteLLM_SpendLogToolIndex": "start_time",
                     "LiteLLM_SpendLogGuardrailIndex": "start_time",
                     "LiteLLM_ErrorLogs": "startTime",
                     "LiteLLM_AuditLog": "updated_at"}
ROLLING_DATE_COLUMN = "date"                      # all LiteLLM_Daily* aggregates
ROLLING_WINDOW_DAYS = 3
WATERMARK_GUARD_S = 60                            # don't export the last 60 s (batch writer race)


def classify(tables: list[str]) -> dict:
    usage = sorted(t for t in tables
                   if t in USAGE_EXACT or any(t.startswith(p) for p in USAGE_PREFIXES))
    transient = sorted(t for t in tables if t in TRANSIENT)
    config = sorted(t for t in tables if t not in set(usage) and t not in TRANSIENT)
    return {"config": config, "usage": usage, "transient": transient}


async def base_tables(conn) -> list[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'")
    return [r["table_name"] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && python -m pytest tests/test_backup_tables.py -q` → PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add ui/app/backup_tables.py ui/tests/test_backup_tables.py
git commit -m "feat(backup): table tier classification"
```

---

### Task 2: Settings & run bookkeeping (`backup_store.py`)

**Files:**
- Create: `ui/app/backup_store.py`
- Test: `ui/tests/test_backup_store.py`

**Interfaces:**
- Produces: `DEFAULTS: dict` (keys `"config"`, `"logs"`); `validate_tier_settings(value: dict) -> dict` (raises `ValueError`); `write_mirror(backup_dir, settings: dict) -> None`; `read_mirror(backup_dir) -> dict | None`; class `BackupStore(dsn)` with `async get_settings() -> dict` (`{"config": {...}, "logs": {...}}`), `async save_settings(tier: str, value: dict)`, `async settings_present() -> bool`, `async start_run(tier: str) -> int`, `async finish_run(run_id, status, path=None, bytes_=0, error=None, meta=None)`, `async runs(tier=None, limit=20) -> list[dict]`, `async last_run(tier, status="ok") -> dict | None`.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing tests (pure parts)**

```python
# ui/tests/test_backup_store.py
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
```

- [ ] **Step 2: Run to verify failure**: `cd ui && python -m pytest tests/test_backup_store.py -q` → FAIL (module missing)

- [ ] **Step 3: Write the implementation**

```python
# ui/app/backup_store.py
"""Backup schedule settings (ui_settings) + run history (ui_backup_runs). Spec §4-5."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Optional
import asyncpg

TIERS = ("config", "logs")
DEFAULTS = {
    "config": {"enabled": True, "frequency": {"kind": "daily"}, "time": "03:00", "retention_days": 14},
    "logs": {"enabled": False, "frequency": {"kind": "daily"}, "time": "03:30", "retention_days": 0},
}
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{1,2})$")


def validate_tier_settings(value: dict) -> dict:
    """Normalize + validate one tier's settings; raises ValueError with a message."""
    out = {"enabled": bool(value.get("enabled", False))}
    freq = value.get("frequency") or {}
    kind = freq.get("kind")
    if kind not in ("daily", "weekly", "every_n_days"):
        raise ValueError("frequency.kind must be daily|weekly|every_n_days")
    f: dict[str, Any] = {"kind": kind}
    if kind == "weekly":
        wd = freq.get("weekday")
        if not isinstance(wd, int) or not 0 <= wd <= 6:
            raise ValueError("frequency.weekday must be 0-6 (Mon=0)")
        f["weekday"] = wd
    if kind == "every_n_days":
        n = freq.get("n")
        if not isinstance(n, int) or not 2 <= n <= 365:
            raise ValueError("frequency.n must be 2-365")
        f["n"] = n
    out["frequency"] = f
    m = _TIME_RE.match(str(value.get("time", "")))
    if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
        raise ValueError("time must be HH:MM (24h)")
    out["time"] = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    rd = value.get("retention_days", 0)
    if not isinstance(rd, int) or rd < 0 or rd > 3650:
        raise ValueError("retention_days must be 0-3650 (0 = keep forever)")
    out["retention_days"] = rd
    return out


def write_mirror(backup_dir: Path | str, settings: dict) -> None:
    d = Path(backup_dir); d.mkdir(mode=0o700, parents=True, exist_ok=True)
    p = d / "settings.json"
    p.write_text(json.dumps(settings, indent=1))
    p.chmod(0o600)


def read_mirror(backup_dir: Path | str) -> Optional[dict]:
    p = Path(backup_dir) / "settings.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


class BackupStore:
    def __init__(self, dsn: str): self._dsn = dsn
    async def _conn(self): return await asyncpg.connect(self._dsn)

    async def ensure_schema(self, conn) -> None:
        await conn.execute('''CREATE TABLE IF NOT EXISTS ui_settings (
            key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz DEFAULT now())''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS ui_backup_runs (
            id bigserial PRIMARY KEY, tier text NOT NULL,
            started_at timestamptz DEFAULT now(), finished_at timestamptz,
            status text NOT NULL, path text, bytes bigint DEFAULT 0, error text, meta jsonb)''')

    async def settings_present(self) -> bool:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return bool(await conn.fetchval(
                "SELECT 1 FROM ui_settings WHERE key LIKE 'backup.%' LIMIT 1"))
        finally: await conn.close()

    async def get_settings(self) -> dict:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            rows = await conn.fetch("SELECT key, value FROM ui_settings WHERE key LIKE 'backup.%'")
            saved = {r["key"].removeprefix("backup."): json.loads(r["value"]) for r in rows}
            return {t: {**DEFAULTS[t], **saved.get(t, {})} for t in TIERS}
        finally: await conn.close()

    async def save_settings(self, tier: str, value: dict) -> None:
        if tier not in TIERS: raise ValueError("tier must be config|logs")
        v = validate_tier_settings(value)
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            await conn.execute('''INSERT INTO ui_settings(key, value) VALUES($1, $2)
                ON CONFLICT(key) DO UPDATE SET value=$2, updated_at=now()''',
                f"backup.{tier}", json.dumps(v))
        finally: await conn.close()

    async def start_run(self, tier: str) -> int:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return int(await conn.fetchval(
                "INSERT INTO ui_backup_runs(tier, status) VALUES($1, 'running') RETURNING id", tier))
        finally: await conn.close()

    async def finish_run(self, run_id: int, status: str, path=None, bytes_=0, error=None, meta=None) -> None:
        conn = await self._conn()
        try:
            await conn.execute('''UPDATE ui_backup_runs SET finished_at=now(), status=$2,
                path=$3, bytes=$4, error=$5, meta=$6 WHERE id=$1''',
                run_id, status, path, int(bytes_), error, json.dumps(meta) if meta is not None else None)
        finally: await conn.close()

    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "tier": r["tier"], "status": r["status"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                "path": r["path"], "bytes": r["bytes"] or 0, "error": r["error"],
                "meta": json.loads(r["meta"]) if r["meta"] else None}

    async def runs(self, tier: Optional[str] = None, limit: int = 20) -> list[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            if tier:
                rows = await conn.fetch("SELECT * FROM ui_backup_runs WHERE tier=$1 "
                                        "ORDER BY id DESC LIMIT $2", tier, limit)
            else:
                rows = await conn.fetch("SELECT * FROM ui_backup_runs ORDER BY id DESC LIMIT $1", limit)
            return [self._row(r) for r in rows]
        finally: await conn.close()

    async def last_run(self, tier: str, status: str = "ok") -> Optional[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            r = await conn.fetchrow("SELECT * FROM ui_backup_runs WHERE tier=$1 AND status=$2 "
                                    "ORDER BY id DESC LIMIT 1", tier, status)
            return self._row(r) if r else None
        finally: await conn.close()
```

- [ ] **Step 4: Add DB-backed tests (skip without Postgres), following `test_config_db.py`'s pattern**

```python
# append to ui/tests/test_backup_store.py
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
```

- [ ] **Step 5: Run**: `cd ui && python -m pytest tests/test_backup_store.py -q` → PASS (DB tests may skip)

- [ ] **Step 6: Commit**

```bash
git add ui/app/backup_store.py ui/tests/test_backup_store.py
git commit -m "feat(backup): settings + run-history store with mirror self-heal file"
```

---

### Task 3: Engine pure helpers (stamps, manifests, pg args, prune)

**Files:**
- Create: `ui/app/backup_engine.py` (pure helpers this task; class in Task 4)
- Test: `ui/tests/test_backup_engine.py`

**Interfaces:**
- Produces: `local_stamp(now: datetime) -> str`; `parse_dsn(dsn: str) -> dict` (`host, port, user, password, dbname`); `pg_dump_cmd(dsn, out_path: str, exclude_tables: list[str]) -> tuple[list[str], dict]`; `pg_restore_cmd(dsn, dump_path: str) -> tuple[list[str], dict]`; `sha256_file(path) -> str`; `build_manifest(tier, taken_at_iso, files: dict[str, Path], **extra) -> dict`; `verify_manifest(dirpath: Path) -> tuple[dict | None, list[str]]`; `prune_candidates(entries: list[tuple[str, str]], retention_days: int, now: datetime) -> list[str]` (entries = `(dir_id, taken_at_iso)`); `fingerprints(salt_key: str | None, fernet_secret: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# ui/tests/test_backup_engine.py
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
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# ui/app/backup_engine.py
"""Backup engine: stamps, manifests, pg_dump/pg_restore commands, prune (spec §3-4)."""
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, unquote


def local_stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H-%M-%S%z")


def parse_dsn(dsn: str) -> dict:
    u = urlsplit(dsn)
    return {"host": u.hostname or "localhost", "port": u.port or 5432,
            "user": unquote(u.username or ""), "password": unquote(u.password or ""),
            "dbname": (u.path or "/").lstrip("/")}


def _pg_env(password: str) -> dict:
    return {**os.environ, "PGPASSWORD": password}


def pg_dump_cmd(dsn: str, out_path: str, exclude_tables: list[str]) -> tuple[list[str], dict]:
    d = parse_dsn(dsn)
    argv = ["pg_dump", "-Fc", "--no-owner", "--no-privileges",
            "-h", d["host"], "-p", str(d["port"]), "-U", d["user"], "-d", d["dbname"],
            "-f", out_path]
    argv += [f'--exclude-table=public."{t}"' for t in exclude_tables]
    return argv, _pg_env(d["password"])


def pg_restore_cmd(dsn: str, dump_path: str) -> tuple[list[str], dict]:
    d = parse_dsn(dsn)
    argv = ["pg_restore", "--data-only", "--disable-triggers", "--no-owner",
            "-h", d["host"], "-p", str(d["port"]), "-U", d["user"], "-d", d["dbname"], dump_path]
    return argv, _pg_env(d["password"])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprints(salt_key: Optional[str], fernet_secret: str) -> dict:
    def fp(v): return hashlib.sha256(v.encode()).hexdigest()[:12] if v else None
    return {"salt": fp(salt_key), "fernet": fp(fernet_secret)}


def build_manifest(tier: str, taken_at_iso: str, files: dict[str, Path], **extra) -> dict:
    return {"tier": tier, "taken_at": taken_at_iso,
            "files": {name: {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
                      for name, p in files.items()},
            **extra}


def verify_manifest(dirpath: Path) -> tuple[Optional[dict], list[str]]:
    mp = dirpath / "manifest.json"
    try:
        m = json.loads(mp.read_text())
    except (OSError, ValueError) as e:
        return None, [f"manifest unreadable: {e}"]
    errs = []
    for name, info in (m.get("files") or {}).items():
        p = dirpath / name
        if not p.is_file():
            errs.append(f"{name}: missing"); continue
        if sha256_file(p) != info.get("sha256"):
            errs.append(f"{name}: sha256 mismatch")
    return m, errs


def prune_candidates(entries: list[tuple[str, str]], retention_days: int, now: datetime) -> list[str]:
    if not retention_days:
        return []
    out = []
    for dir_id, taken_at in entries:
        try:
            age = (now - datetime.fromisoformat(taken_at)).days
        except ValueError:
            continue                     # unparseable manifest date: never auto-delete
        if age > retention_days:
            out.append(dir_id)
    return out
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `git add ui/app/backup_engine.py ui/tests/test_backup_engine.py && git commit -m "feat(backup): engine pure helpers (stamps, manifests, pg cmds, prune)"`

---

### Task 4: Engine runs (`BackupEngine`) — config dump, incremental logs export, snapshots, listing

**Files:**
- Modify: `ui/app/backup_engine.py` (append)
- Test: `ui/tests/test_backup_engine.py` (append)

**Interfaces:**
- Consumes: Task 1 constants/classify; Task 2 `BackupStore`; Task 3 helpers.
- Produces: class `BackupEngine(dsn, backup_dir: str, store: BackupStore, config_store, config_path: str, fernet_secret: str, salt_key: str | None = None, run_subprocess=None, connect=None, now=None)` with `async run_config() -> dict`, `async run_logs() -> dict`, `list_backups() -> dict` (`{"backups": [...], "snapshots": [...]}`), `write_snapshot(items: list[dict]) -> str`, `backup_path(bid: str) -> Path` (validates id grammar, raises `ValueError`), `running: dict[str, bool]`. Injectables: `run_subprocess: async (argv, env) -> (rc:int, stderr_tail:str)`; `connect: async () -> asyncpg-conn-like`; `now: () -> datetime` (aware, local).
  - `config_store` is the existing `app.config_db.ConfigStore` (uses `.applied()`).
- Notes for implementation (bake these in): per-tier `asyncio.Lock` shared per engine *module* (module-level `_LOCKS = {"config": asyncio.Lock(), "logs": asyncio.Lock()}`) so concurrent requests serialize even across engine instances; on lock-not-acquired raise `RuntimeError("already running")`. On any failure: remove the partial backup dir, `finish_run(..., "error", error=...)`, re-raise nothing (return `{"ok": False, "error": ...}`).

- [ ] **Step 1: Write the failing tests (fake subprocess + fake conn)**

```python
# append to ui/tests/test_backup_engine.py
import asyncio, gzip, os
import pytest
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
                r = output(data.encode())
                if asyncio.iscoroutine(r): await r
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
    argv, env = calls[0]
    assert '--exclude-table=public."LiteLLM_SpendLogs"' in argv        # usage excluded
    assert '--exclude-table=public."LiteLLM_HealthCheckTable"' in argv  # transient excluded
    m = json.loads((d / "manifest.json").read_text())
    assert m["tier"] == "config" and m["fingerprints"]["fernet"] and m["item_counts"] == {"model": 1}


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
```

- [ ] **Step 2: Run to verify failure** → `ImportError: cannot import name 'BackupEngine'`

- [ ] **Step 3: Write the implementation (append to `ui/app/backup_engine.py`)**

```python
# append to ui/app/backup_engine.py
import asyncio
import gzip
import re
import shutil
import subprocess
from datetime import timedelta

import asyncpg

from app.backup_tables import (classify, base_tables, WATERMARK_COLUMNS,
                               ROLLING_DATE_COLUMN, ROLLING_WINDOW_DAYS, WATERMARK_GUARD_S)

_ID_RE = re.compile(r"^(config|logs|snapshots)/[A-Za-z0-9+._-]+(/[A-Za-z0-9._-]+)?$")
_LOCKS = {"config": asyncio.Lock(), "logs": asyncio.Lock()}
SNAPSHOT_KEEP = 50


async def _default_run_subprocess(argv: list[str], env: dict) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*argv, env=env,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, err = await p.communicate()
    return p.returncode, (err or b"")[-4096:].decode("utf-8", "replace")


class BackupEngine:
    def __init__(self, dsn, backup_dir, store, config_store, config_path,
                 fernet_secret, salt_key=None, run_subprocess=None, connect=None, now=None):
        self._dsn = dsn
        self._dir = Path(backup_dir)
        self._store = store
        self._config_store = config_store
        self._config_path = config_path
        self._fernet_secret = fernet_secret
        self._salt_key = salt_key
        self._run = run_subprocess or _default_run_subprocess
        self._connect = connect or (lambda: asyncpg.connect(dsn))
        self._now = now or (lambda: datetime.now().astimezone())

    # ---- paths ----
    def backup_path(self, bid: str) -> Path:
        if not _ID_RE.match(bid):
            raise ValueError(f"invalid backup id: {bid!r}")
        p = (self._dir / bid).resolve()
        if not p.is_relative_to(self._dir.resolve()):
            raise ValueError("path escapes backup dir")
        return p

    def _mkdir(self, *parts) -> Path:
        d = self._dir.joinpath(*parts)
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        return d

    @staticmethod
    def _chmod_all(d: Path):
        for f in d.iterdir():
            if f.is_file(): f.chmod(0o600)
        d.chmod(0o700)

    # ---- config tier ----
    async def run_config(self) -> dict:
        if _LOCKS["config"].locked():
            return {"ok": False, "error": "already running"}
        async with _LOCKS["config"]:
            rid = await self._store.start_run("config")
            stamp = local_stamp(self._now())
            d = self._mkdir("config", stamp)
            try:
                conn = await self._connect()
                try:
                    tiers = classify(await base_tables(conn))
                finally:
                    await conn.close()
                exclude = tiers["usage"] + tiers["transient"]
                dump = d / "litellm-config.dump"
                argv, env = pg_dump_cmd(self._dsn, str(dump), exclude)
                rc, err = await self._run(argv, env)
                if rc != 0:
                    raise RuntimeError(f"pg_dump failed (rc={rc}): {err}")
                items = await self._config_store.applied()
                (d / "ui_config.json").write_text(json.dumps({"version": 1, "items": items}, indent=1))
                shutil.copyfile(self._config_path, d / "config.yaml")
                counts: dict[str, int] = {}
                for it in items:
                    counts[it["kind"]] = counts.get(it["kind"], 0) + 1
                files = {f.name: f for f in d.iterdir() if f.name != "manifest.json"}
                m = build_manifest("config", self._now().isoformat(), files,
                                   tables=tiers["config"], excluded=exclude, item_counts=counts,
                                   fingerprints=fingerprints(self._salt_key, self._fernet_secret))
                (d / "manifest.json").write_text(json.dumps(m, indent=1))
                self._chmod_all(d)
                total = sum(f.stat().st_size for f in d.iterdir())
                await self._store.finish_run(rid, "ok", path=f"config/{stamp}", bytes_=total,
                                             meta={"item_counts": counts, "tables": len(tiers["config"])})
                await self._prune("config")
                return {"ok": True, "path": f"config/{stamp}", "bytes": total}
            except Exception as e:
                shutil.rmtree(d, ignore_errors=True)
                await self._store.finish_run(rid, "error", error=str(e)[:2000])
                return {"ok": False, "error": str(e)}

    # ---- logs tier ----
    async def _table_columns(self, conn, table: str) -> set[str]:
        rows = await conn.fetch("SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema='public' AND table_name=$1", table)
        return {r["column_name"] for r in rows}

    def _last_watermarks(self) -> dict:
        """Per-table `to` from the newest logs manifest on disk (spec §3)."""
        logs_dir = self._dir / "logs"
        if not logs_dir.is_dir(): return {}
        for d in sorted(logs_dir.iterdir(), reverse=True):
            m, errs = verify_manifest(d) if (d / "manifest.json").exists() else (None, ["x"])
            if m is not None:
                return {t: v.get("to") for t, v in (m.get("tables") or {}).items() if v.get("to")}
        return {}

    async def run_logs(self) -> dict:
        if _LOCKS["logs"].locked():
            return {"ok": False, "error": "already running"}
        async with _LOCKS["logs"]:
            rid = await self._store.start_run("logs")
            now = self._now()
            stamp = local_stamp(now)
            d = self._mkdir("logs", stamp)
            try:
                marks = self._last_watermarks()
                # SpendLogs timestamps are naive UTC wall-clock — the watermark must be too.
                from datetime import timezone as _tz
                upper = (now - timedelta(seconds=WATERMARK_GUARD_S)).astimezone(_tz.utc).replace(tzinfo=None)
                tables_meta: dict[str, dict] = {}
                conn = await self._connect()
                try:
                    usage = classify(await base_tables(conn))["usage"]
                    for t in usage:
                        cols = await self._table_columns(conn, t)
                        wm_col = WATERMARK_COLUMNS.get(t)
                        if wm_col and wm_col in cols:
                            frm = marks.get(t)
                            where = f'"{wm_col}" > $1 AND "{wm_col}" <= $2' if frm else f'"{wm_col}" <= $1'
                            args = ([datetime.fromisoformat(frm), upper] if frm else [upper])
                            meta = {"mode": "watermark", "from": frm, "to": upper.isoformat()}
                        elif not wm_col and ROLLING_DATE_COLUMN in cols:
                            cutoff = (now - timedelta(days=ROLLING_WINDOW_DAYS)).strftime("%Y-%m-%d")
                            where, args = f'"{ROLLING_DATE_COLUMN}" >= $1', [cutoff]
                            meta = {"mode": "rolling", "from": cutoff, "to": now.strftime("%Y-%m-%d")}
                        else:
                            tables_meta[t] = {"mode": "skipped",
                                              "warning": f"expected column missing ({wm_col or ROLLING_DATE_COLUMN})"}
                            continue
                        n = await conn.fetchval(f'SELECT count(*) FROM "{t}" WHERE {where}', *args)
                        meta["rows"] = int(n or 0)
                        if n:
                            gz = gzip.open(d / f"{t}.csv.gz", "wb")
                            try:
                                def sink(data: bytes): gz.write(data)
                                await conn.copy_from_query(
                                    f'SELECT * FROM "{t}" WHERE {where}', *args,
                                    output=sink, format="csv", header=True)
                            finally:
                                gz.close()
                        tables_meta[t] = meta
                finally:
                    await conn.close()
                files = {f.name: f for f in d.iterdir() if f.name != "manifest.json"}
                m = build_manifest("logs", now.isoformat(), files, tables=tables_meta,
                                   fingerprints=fingerprints(self._salt_key, self._fernet_secret))
                (d / "manifest.json").write_text(json.dumps(m, indent=1))
                self._chmod_all(d)
                total = sum(f.stat().st_size for f in d.iterdir())
                rows_total = sum(v.get("rows", 0) for v in tables_meta.values())
                await self._store.finish_run(rid, "ok", path=f"logs/{stamp}", bytes_=total,
                                             meta={"rows": rows_total, "tables": tables_meta})
                await self._prune("logs")
                return {"ok": True, "path": f"logs/{stamp}", "bytes": total, "rows": rows_total}
            except Exception as e:
                shutil.rmtree(d, ignore_errors=True)
                await self._store.finish_run(rid, "error", error=str(e)[:2000])
                return {"ok": False, "error": str(e)}

    # ---- snapshots ----
    def write_snapshot(self, items: list[dict]) -> str:
        d = self._mkdir("snapshots")
        name = f"{local_stamp(self._now())}-apply.json"
        p = d / name
        p.write_text(json.dumps({"version": 1, "items": items}, indent=1))
        p.chmod(0o600)
        snaps = sorted(d.glob("*.json"))
        for old in snaps[:-SNAPSHOT_KEEP]:
            old.unlink(missing_ok=True)
        return f"snapshots/{name}"

    # ---- listing & prune ----
    def list_backups(self) -> dict:
        backups = []
        for tier in ("config", "logs"):
            td = self._dir / tier
            if not td.is_dir(): continue
            for d in sorted(td.iterdir(), reverse=True):
                if not (d / "manifest.json").is_file(): continue
                try:
                    m = json.loads((d / "manifest.json").read_text())
                except ValueError:
                    continue
                backups.append({"id": f"{tier}/{d.name}", "tier": tier,
                                "taken_at": m.get("taken_at"),
                                "bytes": sum(f.stat().st_size for f in d.iterdir()),
                                "files": sorted(f.name for f in d.iterdir()),
                                "summary": m.get("item_counts") or
                                           {t: v.get("rows") for t, v in (m.get("tables") or {}).items()
                                            if isinstance(v, dict) and "rows" in v}})
        snaps = []
        sd = self._dir / "snapshots"
        if sd.is_dir():
            for p in sorted(sd.glob("*.json"), reverse=True):
                snaps.append({"id": f"snapshots/{p.name}", "taken_at": p.name.split("-apply")[0],
                              "bytes": p.stat().st_size})
        return {"backups": backups, "snapshots": snaps}

    @property
    def running(self) -> dict:
        return {t: lock.locked() for t, lock in _LOCKS.items()}

    async def _prune(self, tier: str) -> None:
        # Retention is read through BackupStore; fakes without get_settings skip pruning.
        try:
            retention = (await self._store.get_settings())[tier]["retention_days"]
        except Exception:
            return
        td = self._dir / tier
        if not td.is_dir(): return
        entries = []
        for d in td.iterdir():
            mp = d / "manifest.json"
            if mp.is_file():
                try:
                    entries.append((f"{tier}/{d.name}", json.loads(mp.read_text()).get("taken_at") or ""))
                except ValueError:
                    continue
        for bid in prune_candidates(entries, retention, self._now()):
            shutil.rmtree(self._dir / bid, ignore_errors=True)
```

Note: `FakeRunStore` in the tests has no `get_settings`, so `_prune` must swallow that (`except Exception: return`) — the code above does. Delete the leftover first line of `_prune` (`settings = ...`) if the reviewer flags it; the retention read via `self._store.get_settings()` is the real path.

- [ ] **Step 4: Run**: `cd ui && python -m pytest tests/test_backup_engine.py -q` → PASS
- [ ] **Step 5: Run the whole suite**: `cd ui && python -m pytest tests/ -q` → PASS
- [ ] **Step 6: Commit** `git add -A ui/app/backup_engine.py ui/tests/test_backup_engine.py && git commit -m "feat(backup): engine runs — config dump, incremental logs export, snapshots, prune"`

---

### Task 5: Scheduler wiring (`backup_scheduler.py`, `settings.py`, `main.py`)

**Files:**
- Create: `ui/app/backup_scheduler.py`
- Modify: `ui/app/settings.py` (add `backup_dir: str = "/backups"`)
- Modify: `ui/app/main.py` (lifespan: self-heal + job registration)
- Test: `ui/tests/test_backup_scheduler.py`

**Interfaces:**
- Consumes: `BackupStore` (Task 2), `BackupEngine` factory (callable returning an engine).
- Produces: `build_trigger(tier_settings: dict, now: datetime) -> CronTrigger | IntervalTrigger`; `next_hhmm(now: datetime, hhmm: str) -> datetime`; `async register_backup_jobs(sched, store: BackupStore, engine_factory) -> None` (job ids `backup-config`, `backup-logs`, `replace_existing=True`; removes the job when a tier is disabled); `set_scheduler(sched)` / `get_scheduler()` module accessors; `next_fire(tier: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# ui/tests/test_backup_scheduler.py
from datetime import datetime, timedelta, timezone
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from app.backup_scheduler import build_trigger, next_hhmm

TZ = timezone(timedelta(hours=10))
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=TZ)


def test_daily_and_weekly_are_cron():
    t = build_trigger({"frequency": {"kind": "daily"}, "time": "03:15"}, NOW)
    assert isinstance(t, CronTrigger)
    w = build_trigger({"frequency": {"kind": "weekly", "weekday": 2}, "time": "04:00"}, NOW)
    assert isinstance(w, CronTrigger)


def test_every_n_days_is_interval_starting_next_hhmm():
    t = build_trigger({"frequency": {"kind": "every_n_days", "n": 3}, "time": "03:00"}, NOW)
    assert isinstance(t, IntervalTrigger)
    assert t.interval == timedelta(days=3)
    assert t.start_date == datetime(2026, 8, 25, 3, 0, tzinfo=NOW.tzinfo)  # 03:00 already past today


def test_next_hhmm_today_when_future():
    assert next_hhmm(NOW, "13:30") == NOW.replace(hour=13, minute=30, second=0, microsecond=0)
    assert next_hhmm(NOW, "11:00") == (NOW + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
```

- [ ] **Step 2: Run** → FAIL (module missing)

- [ ] **Step 3: Implementation**

```python
# ui/app/backup_scheduler.py
"""APScheduler wiring for the two backup tiers (spec §5)."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger("uvicorn.error")
_JOB_IDS = {"config": "backup-config", "logs": "backup-logs"}
_scheduler = None


def set_scheduler(s) -> None:
    global _scheduler; _scheduler = s


def get_scheduler():
    return _scheduler


def next_hhmm(now: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return cand if cand > now else cand + timedelta(days=1)


def build_trigger(tier_settings: dict, now: datetime):
    freq = tier_settings["frequency"]
    h, m = (int(x) for x in tier_settings["time"].split(":"))
    if freq["kind"] == "daily":
        return CronTrigger(hour=h, minute=m)
    if freq["kind"] == "weekly":
        return CronTrigger(day_of_week=freq["weekday"], hour=h, minute=m)
    return IntervalTrigger(days=freq["n"], start_date=next_hhmm(now, tier_settings["time"]))


async def register_backup_jobs(sched, store, engine_factory) -> None:
    """Add/replace/remove the two backup jobs to match current settings."""
    settings = await store.get_settings()
    now = datetime.now().astimezone()
    for tier, job_id in _JOB_IDS.items():
        if sched.get_job(job_id):
            sched.remove_job(job_id)
        if not settings[tier]["enabled"]:
            continue

        async def job(tier=tier):
            try:
                eng = engine_factory()
                out = await (eng.run_config() if tier == "config" else eng.run_logs())
                if not out.get("ok"):
                    log.warning("scheduled %s backup failed: %s", tier, out.get("error"))
            except Exception:
                log.exception("scheduled %s backup crashed", tier)

        sched.add_job(job, build_trigger(settings[tier], now), id=job_id, replace_existing=True)


def next_fire(tier: str) -> Optional[str]:
    s = get_scheduler()
    if not s: return None
    j = s.get_job(_JOB_IDS[tier])
    return j.next_run_time.isoformat() if j and j.next_run_time else None
```

- [ ] **Step 4: Wire `settings.py` and `main.py`**

`ui/app/settings.py`: after `catalog_endpoints_url`, add:

```python
    backup_dir: str = "/backups"   # local mount for scheduled backups (BACKUP_DIR)
```

`ui/app/main.py` lifespan, after the catalog block and before `if sched: sched.start()`:

```python
    if s.database_url:
        from app.backup_store import BackupStore, read_mirror, write_mirror
        from app.backup_scheduler import register_backup_jobs, set_scheduler
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        bstore = BackupStore(s.database_url)
        try:
            # Self-heal: ui_settings empty + mirror present (e.g. after a ui_* wipe) → re-import.
            if not await bstore.settings_present():
                mirror = read_mirror(s.backup_dir)
                if mirror:
                    for tier in ("config", "logs"):
                        if tier in mirror:
                            await bstore.save_settings(tier, mirror[tier])
                    logging.getLogger(__name__).warning("backup settings restored from %s/settings.json", s.backup_dir)
            sched = sched or AsyncIOScheduler()
            from app.routes.backup_routes import make_backup_engine
            await register_backup_jobs(sched, bstore, make_backup_engine)
            set_scheduler(sched)
        except Exception:
            logging.getLogger(__name__).warning("backup scheduler setup failed", exc_info=True)
```

(`make_backup_engine` arrives in Task 9; to keep this task green, create a minimal placeholder module now — Task 9 replaces it:)

```python
# ui/app/routes/backup_routes.py  (placeholder created in Task 5, completed in Task 9)
from fastapi import APIRouter
router = APIRouter(prefix="/api")


def make_backup_engine():
    from app.settings import get_settings
    from app.backup_engine import BackupEngine
    from app.backup_store import BackupStore
    from app.config_db import ConfigStore
    s = get_settings()
    return BackupEngine(s.database_url, s.backup_dir, BackupStore(s.database_url),
                        ConfigStore(s.database_url), s.config_path,
                        fernet_secret=(s.credentials_key or s.session_secret),
                        salt_key=None)
```

- [ ] **Step 5: Run**: `cd ui && python -m pytest tests/test_backup_scheduler.py tests/ -q` → PASS (whole suite still green — lifespan changes are guarded by `database_url` and exceptions are swallowed)
- [ ] **Step 6: Commit** `git add -A ui/app ui/tests/test_backup_scheduler.py && git commit -m "feat(backup): scheduler wiring with mirror self-heal on boot"`

---

### Task 6: Rollback restore + `ConfigStore.replace_applied`

**Files:**
- Create: `ui/app/backup_restore.py`
- Modify: `ui/app/config_db.py` (add `replace_applied`)
- Test: `ui/tests/test_backup_restore.py`, extend `ui/tests/test_config_db.py`

**Interfaces:**
- Consumes: `effective`/`render_config` (`app.config_render`), `reconcile_models` (`app.model_reconcile`), `reconcile_mcp`+`build_desired` (`app.mcp_reconcile`), `_make_resolve_key` (`app.config_engine`), `write_config_atomic` (`app.config_store`), Reloader.
- Produces: `rollback_preview(current: list[dict], new: list[dict]) -> dict` (`{"added": [...], "removed": [...], "changed": [...], "restart_kinds_changed": bool}` where entries are `{"kind","name"}`); `check_decryptable(items, fernet) -> list[str]`; `parse_export(text: str) -> list[dict]` (raises `ValueError`); `async rollback_config(items, *, config_store, models_client, mcp_client, reloader, config_path, fernet) -> dict`; `ConfigStore.replace_applied(items: list[dict]) -> None` (one transaction: delete applied, insert items, delete staged).

- [ ] **Step 1: Write the failing tests**

```python
# ui/tests/test_backup_restore.py
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
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implementation**

```python
# ui/app/backup_restore.py
"""Restore flows: rollback (hot), full recovery (cold), logs merge (spec §6)."""
from __future__ import annotations
import json
from typing import Any

import yaml

from app.config_render import render_config
from app.config_engine import _make_resolve_key
from app.config_store import write_config_atomic
from app.model_reconcile import reconcile_models
from app.mcp_reconcile import build_desired as build_mcp_desired, mcp_content_diff, reconcile_mcp

RESTART_KINDS = {"router_setting", "litellm_setting", "general_setting", "passthrough"}


def parse_export(text: str) -> list[dict]:
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not valid JSON: {e}")
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ValueError("expected {version, items: [...]} export shape")
    for it in items:
        if not isinstance(it, dict) or "kind" not in it or "name" not in it or "data" not in it:
            raise ValueError("every item needs kind, name, data")
    return items


def rollback_preview(current: list[dict], new: list[dict]) -> dict:
    cur = {(i["kind"], i["name"]): i["data"] for i in current}
    nxt = {(i["kind"], i["name"]): i["data"] for i in new}
    added = sorted(set(nxt) - set(cur))
    removed = sorted(set(cur) - set(nxt))
    changed = sorted(k for k in set(cur) & set(nxt) if cur[k] != nxt[k])
    touched = added + removed + changed
    return {"added": [{"kind": k, "name": n} for k, n in added],
            "removed": [{"kind": k, "name": n} for k, n in removed],
            "changed": [{"kind": k, "name": n} for k, n in changed],
            "restart_kinds_changed": any(k in RESTART_KINDS for k, _ in touched)}


def check_decryptable(items: list[dict], fernet) -> list[str]:
    bad = []
    for it in items:
        d = it.get("data") or {}
        for field in ("value_encrypted", "auth_value_encrypted"):
            v = d.get(field)
            if v:
                try:
                    fernet.decrypt(v.encode())
                except Exception:
                    bad.append(f'{it["kind"]}/{it["name"]}')
                break
    return bad


async def rollback_config(items: list[dict], *, config_store, models_client, mcp_client,
                          reloader, config_path: str, fernet) -> dict:
    """Replace the master with `items`, then converge exactly like resync + a
    settings-diff-driven restart (spec §6.1). Caller has already run pre-checks."""
    dec = lambda b: fernet.decrypt(b.encode()).decode()
    await config_store.replace_applied(items)
    applied = await config_store.applied()

    model_items = [it for it in applied if it["kind"] == "model"]
    resolve_key = _make_resolve_key(applied, dec)
    live = await models_client.list_models()
    model_report = await reconcile_models(model_items, live, models_client,
                                          changed_item_names={it["name"] for it in model_items},
                                          creds_changed=set(), resolve_key=resolve_key,
                                          converge_content=True)
    mcp_items = [it for it in applied if it["kind"] == "mcp_server"]
    try:
        mcp_live = await mcp_client.list_servers()
        desired_mcp, _ = build_mcp_desired(mcp_items, None)
        live_by_id = {s.get("server_id"): s for s in mcp_live if s.get("server_id")}
        drifted = {sid for sid in (set(desired_mcp) & set(live_by_id))
                   if mcp_content_diff(desired_mcp[sid], live_by_id[sid])}
        mcp_report = await reconcile_mcp(mcp_items, mcp_live, mcp_client, drifted, dec)
    except Exception as e:
        mcp_report = {"added": 0, "updated": 0, "deleted": 0,
                      "failed": [{"id": "*", "op": "list", "error": str(e)}]}

    out: dict[str, Any] = {"applied": True, "models": model_report, "mcp": mcp_report}
    rendered = render_config(applied, dec, hybrid=True)
    try:
        on_disk = yaml.safe_load(open(config_path)) or {}
    except OSError:
        on_disk = None
    if rendered != on_disk:
        write_config_atomic(config_path, yaml.safe_dump(rendered, sort_keys=False))
        expected = [(it["data"] or {}).get("model_name", it["name"]) for it in model_items]
        try:
            await reloader.reload_and_verify(expected)
            out["restart"] = "healthy"
        except Exception as e:
            out["restart"] = "unhealthy"; out["detail"] = str(e)
    else:
        out["restart"] = "skipped"
    return out
```

`ui/app/config_db.py` — add inside `ConfigStore`:

```python
    async def replace_applied(self, items: list[dict]) -> None:
        """Restore path: atomically replace the whole master and discard staged."""
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            async with conn.transaction():
                await conn.execute(f"DELETE FROM {APPLIED}")
                for it in items:
                    await conn.execute(
                        f"INSERT INTO {APPLIED}(kind,name,data) VALUES($1,$2,$3)",
                        it["kind"], it["name"], json.dumps(it["data"]))
                await conn.execute(f"DELETE FROM {STAGED}")
        finally: await conn.close()
```

And in `ui/tests/test_config_db.py` append:

```python
async def test_replace_applied_swaps_master_and_clears_staged(store):
    await store.stage("model", "old", {"model_name": "o"})
    await store.fold()
    await store.stage("model", "pending", {"model_name": "p"})
    await store.replace_applied([{"kind": "model", "name": "new", "data": {"model_name": "n"}}])
    applied = await store.applied()
    assert [(i["kind"], i["name"]) for i in applied] == [("model", "new")]
    assert await store.staged() == []
```

- [ ] **Step 4: Run**: `cd ui && python -m pytest tests/test_backup_restore.py tests/test_config_db.py -q` → PASS (DB test may skip)
- [ ] **Step 5: Commit** `git add -A ui/app ui/tests && git commit -m "feat(backup): rollback restore with preview, decrypt pre-check, replace_applied"`

---

### Task 7: Full recovery + Reloader stop/start

**Files:**
- Modify: `ui/app/backup_restore.py` (append), `ui/app/reloader.py`
- Test: `ui/tests/test_backup_restore.py` (append); keep `ui/tests/test_reloader.py` green

**Interfaces:**
- Produces (reloader): `async Reloader.stop()`, `async Reloader.start()`, `async Reloader.verify(expected_models: list[str]) -> bool` (the existing poll loop, no trigger); `reload_and_verify` becomes `await self.trigger(); return await self.verify(expected)` — behavior unchanged.
- Produces (restore): `truncate_statement(tables: list[str]) -> str` (skips `NEVER_RESTORE`, double-quotes names); `check_fingerprints(manifest: dict, salt_key, fernet_secret) -> list[str]`; `async full_recovery(bdir: Path, *, dsn, reloader, config_path, connect, run_subprocess) -> dict` returning `{"ok": bool, "steps": [{"step","status","detail"}]}` with steps `verify_backup, fingerprints, stop, truncate, pg_restore, config_yaml, start, ready`.

- [ ] **Step 1: Write the failing tests**

```python
# append to ui/tests/test_backup_restore.py
import gzip
from datetime import datetime
from pathlib import Path
from app.backup_restore import truncate_statement, check_fingerprints, full_recovery
from app.backup_engine import build_manifest, fingerprints as make_fps, pg_dump_cmd  # reuse helpers

pytestmark = pytest.mark.asyncio


def test_truncate_statement_skips_prisma_and_quotes():
    sql = truncate_statement(["LiteLLM_TeamTable", "_prisma_migrations", "ui_config_applied"])
    assert sql == 'TRUNCATE "LiteLLM_TeamTable", "ui_config_applied"'


def test_check_fingerprints():
    m = {"fingerprints": make_fps("salt", "fern")}
    assert check_fingerprints(m, "salt", "fern") == []
    assert check_fingerprints(m, "other", "fern") == ["salt"]
    assert check_fingerprints({}, "salt", "fern") == []          # old manifest: no check possible


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
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Refactor `ui/app/reloader.py`** — split `reload_and_verify` and add stop/start:

```python
    async def stop(self) -> None:
        async with self._client(self._trigger_timeout) as c:
            r = await c.post(f"{self._sock}/containers/{self._container}/stop", params={"t": 30})
            if r.status_code >= 400 and r.status_code != 304:   # 304 = already stopped
                raise ReloadError(f"stop failed: {r.status_code} {r.text[:200]}")

    async def start(self) -> None:
        async with self._client(self._trigger_timeout) as c:
            r = await c.post(f"{self._sock}/containers/{self._container}/start")
            if r.status_code >= 400 and r.status_code != 304:   # 304 = already started
                raise ReloadError(f"start failed: {r.status_code} {r.text[:200]}")

    async def verify(self, expected_models: list[str]) -> bool:
        deadline = time.monotonic() + self._timeout
        last = "no probe yet"
        while time.monotonic() < deadline:
            ...                          # move the existing loop body here VERBATIM
        raise ReloadError(f"proxy did not converge within {self._timeout}s ({last})")

    async def reload_and_verify(self, expected_models: list[str]) -> bool:
        await self.trigger()
        return await self.verify(expected_models)
```

Run `cd ui && python -m pytest tests/test_reloader.py -q` after the refactor — it must stay green (pure extraction).

- [ ] **Step 4: Append to `ui/app/backup_restore.py`**

```python
from pathlib import Path
from app.backup_engine import verify_manifest, pg_restore_cmd, fingerprints as _fps
from app.backup_tables import NEVER_RESTORE, classify, base_tables


def truncate_statement(tables: list[str]) -> str:
    keep = [t for t in tables if t not in NEVER_RESTORE]
    return "TRUNCATE " + ", ".join(f'"{t}"' for t in keep)


def check_fingerprints(manifest: dict, salt_key, fernet_secret) -> list[str]:
    want = manifest.get("fingerprints") or {}
    have = _fps(salt_key, fernet_secret)
    return [k for k in ("salt", "fernet")
            if want.get(k) and have.get(k) and want[k] != have[k]]


async def full_recovery(bdir: Path, *, dsn, reloader, config_path, connect,
                        run_subprocess, salt_key, fernet_secret) -> dict:
    steps: list[dict] = []
    def step(name, status, detail=""):
        steps.append({"step": name, "status": status, "detail": detail})
        return status == "ok"

    manifest, errs = verify_manifest(bdir)
    if manifest is None or errs:
        step("verify_backup", "error", "; ".join(errs or ["no manifest"]))
        return {"ok": False, "steps": steps}
    step("verify_backup", "ok")

    mism = check_fingerprints(manifest, salt_key, fernet_secret)
    if mism:
        step("fingerprints", "error",
             f"backup was made under different secrets ({', '.join(mism)}) — refusing")
        return {"ok": False, "steps": steps}
    step("fingerprints", "ok")

    ok = True
    try:
        await reloader.stop(); step("stop", "ok")
    except Exception as e:
        step("stop", "error", str(e))
        return {"ok": False, "steps": steps}
    try:
        conn = await connect()
        try:
            live_config = set(classify(await base_tables(conn))["config"])
            targets = [t for t in manifest.get("tables", []) if t in live_config]
            missing = [t for t in manifest.get("tables", []) if t not in live_config]
            await conn.execute(truncate_statement(targets))
            step("truncate", "ok", f"skipped (not live): {missing}" if missing else "")
        finally:
            await conn.close()
        argv, env = pg_restore_cmd(dsn, str(bdir / "litellm-config.dump"))
        rc, err = await run_subprocess(argv, env)
        if rc != 0:
            ok = step("pg_restore", "error", err) and ok
        else:
            step("pg_restore", "ok")
        if ok:
            from app.config_store import write_config_atomic
            write_config_atomic(config_path, (bdir / "config.yaml").read_text())
            step("config_yaml", "ok")
    except Exception as e:
        ok = step("recover", "error", str(e)) and ok
    finally:
        try:
            await reloader.start(); step("start", "ok")
            await reloader.verify([]); step("ready", "ok")
        except Exception as e:
            ok = step("start", "error", str(e)) and ok
    return {"ok": ok and all(s["status"] == "ok" for s in steps), "steps": steps}
```

- [ ] **Step 5: Run**: `cd ui && python -m pytest tests/test_backup_restore.py tests/test_reloader.py -q` → PASS
- [ ] **Step 6: Commit** `git add -A ui/app ui/tests && git commit -m "feat(backup): full recovery (data-only, step log) + reloader stop/start/verify split"`

---

### Task 8: Logs merge restore

**Files:**
- Modify: `ui/app/backup_restore.py` (append)
- Test: `ui/tests/test_backup_restore.py` (append)

**Interfaces:**
- Produces: `merge_sql(table: str, csv_cols: list[str], live_cols: list[str]) -> dict` (`{"temp": str, "copy_columns": [...], "insert": str, "dropped": [...]}`); `async restore_logs(slice_dirs: list[Path], connect) -> dict` (`{"ok": bool, "tables": {name: {"inserted": int, "skipped": int, "dropped_columns": [...]}}}`). Merge = `INSERT ... SELECT ... ON CONFLICT DO NOTHING` (no conflict target — any unique violation is skipped).

- [ ] **Step 1: Write the failing tests**

```python
# append to ui/tests/test_backup_restore.py
from app.backup_restore import merge_sql, restore_logs


def test_merge_sql_drops_unknown_cols_and_targets_any_conflict():
    m = merge_sql("LiteLLM_SpendLogs", ["request_id", "gone_col", "spend"],
                  ["request_id", "spend", "extra_live"])
    assert m["copy_columns"] == ["request_id", "spend"]
    assert m["dropped"] == ["gone_col"]
    assert m["temp"] == 'CREATE TEMP TABLE _restore (LIKE "LiteLLM_SpendLogs" INCLUDING DEFAULTS) ON COMMIT DROP'
    assert m["insert"] == ('INSERT INTO "LiteLLM_SpendLogs" ("request_id", "spend") '
                           'SELECT "request_id", "spend" FROM _restore ON CONFLICT DO NOTHING')


class MergeConn:
    def __init__(self): self.copied = []; self.sql = []
    def transaction(self):
        conn = self
        class _T:
            async def __aenter__(self): return None
            async def __aexit__(self, *a): return False
        return _T()
    async def fetch(self, q, *a):
        return [{"column_name": c} for c in ["request_id", "spend"]]
    async def execute(self, q, *a):
        self.sql.append(q)
        return "INSERT 0 1" if q.startswith("INSERT") else "OK"
    async def copy_to_table(self, table, *, source=None, columns=None, format=None, header=None, **kw):
        self.copied.append((table, tuple(columns)))
        return "COPY 2"
    async def close(self): pass


async def test_restore_logs_merges_and_counts(tmp_path):
    d = tmp_path / "logs" / "s1"; d.mkdir(parents=True)
    with gzip.open(d / "LiteLLM_SpendLogs.csv.gz", "wt") as f:
        f.write("request_id,gone_col,spend\nr1,x,0.1\nr2,y,0.2\n")
    (d / "manifest.json").write_text(json.dumps({"tier": "logs", "taken_at": "t", "files": {}}))
    conn = MergeConn()
    async def connect(): return conn
    out = await restore_logs([d], connect)
    assert out["ok"] is True
    t = out["tables"]["LiteLLM_SpendLogs"]
    assert t["inserted"] == 1 and t["skipped"] == 1 and t["dropped_columns"] == ["gone_col"]
    assert conn.copied == [("_restore", ("request_id", "spend"))]
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implementation (append to `ui/app/backup_restore.py`)**

```python
import csv as _csv
import gzip as _gzip
import io as _io


def merge_sql(table: str, csv_cols: list[str], live_cols: list[str]) -> dict:
    live = set(live_cols)
    used = [c for c in csv_cols if c in live]
    dropped = [c for c in csv_cols if c not in live]
    cols = ", ".join(f'"{c}"' for c in used)
    return {"temp": f'CREATE TEMP TABLE _restore (LIKE "{table}" INCLUDING DEFAULTS) ON COMMIT DROP',
            "copy_columns": used, "dropped": dropped,
            "insert": (f'INSERT INTO "{table}" ({cols}) SELECT {cols} FROM _restore '
                       f'ON CONFLICT DO NOTHING')}


def _tag_count(tag: str) -> int:
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError):
        return 0


async def restore_logs(slice_dirs: list, connect) -> dict:
    tables: dict[str, dict] = {}
    ok = True
    conn = await connect()
    try:
        for d in slice_dirs:
            for gz in sorted(Path(d).glob("*.csv.gz")):
                table = gz.name.removesuffix(".csv.gz")
                try:
                    with _gzip.open(gz, "rt", newline="") as f:
                        header = next(_csv.reader(f))
                    live_cols = [r["column_name"] for r in await conn.fetch(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position", table)]
                    if not live_cols:
                        tables.setdefault(table, {"inserted": 0, "skipped": 0,
                                                  "dropped_columns": [], "error": "table not live"})
                        continue
                    m = merge_sql(table, header, live_cols)
                    async with conn.transaction():
                        await conn.execute(m["temp"])
                        copy_tag = await conn.copy_to_table(
                            "_restore", source=_gzip.open(gz, "rb"),
                            columns=m["copy_columns"], format="csv", header=True)
                        copied = _tag_count(str(copy_tag))
                        inserted = _tag_count(await conn.execute(m["insert"]))
                    agg = tables.setdefault(table, {"inserted": 0, "skipped": 0,
                                                    "dropped_columns": m["dropped"]})
                    agg["inserted"] += inserted
                    agg["skipped"] += max(copied - inserted, 0)
                except Exception as e:
                    ok = False
                    tables.setdefault(table, {"inserted": 0, "skipped": 0,
                                              "dropped_columns": []})["error"] = str(e)
    finally:
        await conn.close()
    return {"ok": ok, "tables": tables}
```

Note on `copy_to_table` columns: copying a CSV whose column ORDER differs from the temp table requires `columns=` to name the CSV's used columns in file order — asyncpg passes it through to `COPY _restore (cols...) FROM STDIN`. The CSV keeps its original `SELECT *` order, which matches the live table's ordinal order for the used columns; dropped columns break contiguity, so when `m["dropped"]` is non-empty the implementation must fall back to re-writing the CSV in memory keeping only `copy_columns` (read with `csv.reader`, write with `csv.writer` into an `io.BytesIO`, pass that as `source`). Implement this fallback exactly:

```python
                        src = _gzip.open(gz, "rb")
                        if m["dropped"]:
                            keep_idx = [header.index(c) for c in m["copy_columns"]]
                            buf = _io.StringIO()
                            w = _csv.writer(buf)
                            with _gzip.open(gz, "rt", newline="") as f2:
                                r = _csv.reader(f2)
                                for row in r:
                                    w.writerow([row[i] for i in keep_idx])
                            src = _io.BytesIO(buf.getvalue().encode())
                        copy_tag = await conn.copy_to_table(
                            "_restore", source=src,
                            columns=m["copy_columns"], format="csv", header=True)
```

(replace the plain `copy_to_table` call above with this block).

- [ ] **Step 4: Run**: `cd ui && python -m pytest tests/test_backup_restore.py -q` → PASS
- [ ] **Step 5: Commit** `git add -A ui/app ui/tests && git commit -m "feat(backup): logs merge restore (temp-table COPY + ON CONFLICT DO NOTHING)"`

---

### Task 9: `/api/backup/*` routes

**Files:**
- Replace: `ui/app/routes/backup_routes.py` (placeholder from Task 5)
- Modify: `ui/app/main.py` (include router)
- Test: `ui/tests/test_backup_routes.py`

**Interfaces:**
- Consumes: everything from Tasks 1-8; factories mirror `config_v3_routes` style so tests can monkeypatch `backup_routes.make_backup_engine`, `make_backup_store`, `make_models_client`, `make_config_store`, `make_reloader`.
- Produces the exact spec §9 routes. Status shape:
  `{"tiers": {"config": {...}, "logs": {...}}, "master_models": int|None, "live_models": int|None, "master_empty_live_nonempty": bool}` where each tier is `{"enabled", "last_ok", "last_error", "running", "next_run", "stale"}`.

- [ ] **Step 1: Write the failing tests**

```python
# ui/tests/test_backup_routes.py
import os, json, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.auth import hash_password


class FakeEngine:
    def __init__(self, tmp):
        self.dir = Path(tmp); self.ran = []
        self.running = {"config": False, "logs": False}
    def backup_path(self, bid):
        from app.backup_engine import _ID_RE
        if not _ID_RE.match(bid): raise ValueError("bad id")
        return self.dir / bid
    async def run_config(self): self.ran.append("config"); return {"ok": True, "path": "config/x"}
    async def run_logs(self): self.ran.append("logs"); return {"ok": True, "path": "logs/x"}
    def list_backups(self): return {"backups": [{"id": "config/x", "tier": "config"}], "snapshots": []}


class FakeBStore:
    def __init__(self):
        from app.backup_store import DEFAULTS
        self.settings = {"config": dict(DEFAULTS["config"]), "logs": dict(DEFAULTS["logs"])}
        self.saved = []
    async def get_settings(self): return self.settings
    async def save_settings(self, tier, value): self.saved.append((tier, value))
    async def runs(self, tier=None, limit=20): return []
    async def last_run(self, tier, status="ok"): return None


class FakeModels:
    def __init__(self, n): self.n = n
    async def list_models(self):
        return [{"model_name": f"m{i}", "model_info": {"id": str(i)}} for i in range(self.n)]


class FakeCStore:
    def __init__(self, items): self.items = items
    async def applied(self): return self.items


def _client(tmp_path, engine=None, bstore=None, models=None, cstore=None):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path / "c.yaml"), DATABASE_URL="postgresql://x",
                      BACKUP_DIR=str(tmp_path / "backups"))
    (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.backup_routes as br
    br.make_backup_engine = lambda: engine or FakeEngine(tmp_path / "backups")
    br.make_backup_store = lambda: bstore or FakeBStore()
    br.make_models_client = lambda: models or FakeModels(0)
    br.make_config_store = lambda: cstore or FakeCStore([])
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


def test_backup_routes_require_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.get("/api/backup/status").status_code == 401


def test_status_flags_empty_master_with_live_models(tmp_path):
    c = _client(tmp_path, models=FakeModels(3), cstore=FakeCStore([]))
    d = c.get("/api/backup/status").json()
    assert d["master_empty_live_nonempty"] is True and d["live_models"] == 3
    assert set(d["tiers"]) == {"config", "logs"}


def test_settings_put_validates_and_saves(tmp_path):
    bs = FakeBStore()
    c = _client(tmp_path, bstore=bs)
    r = c.put("/api/backup/settings", json={"config": {"enabled": True,
        "frequency": {"kind": "daily"}, "time": "02:00", "retention_days": 7}})
    assert r.status_code == 200 and bs.saved and bs.saved[0][0] == "config"
    r2 = c.put("/api/backup/settings", json={"config": {"frequency": {"kind": "hourly"}, "time": "02:00"}})
    assert r2.status_code == 422


def test_run_now_and_list(tmp_path):
    eng = FakeEngine(tmp_path / "b")
    c = _client(tmp_path, engine=eng)
    assert c.post("/api/backup/run", json={"tier": "logs"}).json()["ok"] is True
    assert eng.ran == ["logs"]
    assert c.post("/api/backup/run", json={"tier": "nope"}).status_code == 422
    assert c.get("/api/backup/list").json()["backups"][0]["id"] == "config/x"


def test_confirmation_strings_enforced(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/backup/rollback", json={"source": "snapshots/x.json", "confirm": "no"}).status_code == 422
    assert c.post("/api/backup/recover", json={"source": "config/x", "confirm": "no"}).status_code == 422
    assert c.post("/api/backup/restore-logs", json={"source": "all", "confirm": "no"}).status_code == 422


def test_download_rejects_traversal_and_missing(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/backup/download", params={"path": "config/../../etc/passwd"}).status_code == 422
    assert c.get("/api/backup/download", params={"path": "config/none/file"}).status_code == 404
```

- [ ] **Step 2: Run** → FAIL (routes missing)

- [ ] **Step 3: Implementation — replace `ui/app/routes/backup_routes.py`**

```python
# ui/app/routes/backup_routes.py
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth import login_required
from app.settings import get_settings
from app.backup_engine import BackupEngine, verify_manifest
from app.backup_store import BackupStore, TIERS, validate_tier_settings, write_mirror
from app.backup_restore import (parse_export, rollback_preview, check_decryptable,
                                rollback_config, full_recovery, restore_logs)
from app.backup_scheduler import register_backup_jobs, get_scheduler, next_fire
from app.config_db import ConfigStore
from app.credentials_store import fernet_from_secret
from app.models_client import ModelsClient
from app.mcp_client import McpClient
from app.reloader import Reloader

log = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api")


def make_backup_store() -> BackupStore:
    s = get_settings()
    if not s.database_url: raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return BackupStore(s.database_url)


def make_config_store() -> ConfigStore:
    return ConfigStore(get_settings().database_url)


def make_models_client() -> ModelsClient:
    s = get_settings(); return ModelsClient(s.litellm_base_url, s.litellm_master_key)


def make_mcp_client() -> McpClient:
    s = get_settings(); return McpClient(s.litellm_base_url, s.litellm_master_key)


def make_reloader() -> Reloader:
    s = get_settings()
    return Reloader(s.socket_proxy_url, s.litellm_base_url, s.litellm_master_key,
                    s.litellm_container, mode=s.reload_mode, timeout_s=s.reload_timeout_s)


def make_backup_engine() -> BackupEngine:
    s = get_settings()
    return BackupEngine(s.database_url, s.backup_dir, make_backup_store(),
                        make_config_store(), s.config_path,
                        fernet_secret=(s.credentials_key or s.session_secret), salt_key=None)


def _fernet():
    s = get_settings(); return fernet_from_secret(s.credentials_key or s.session_secret)


def _interval_days(tier_settings: dict) -> int:
    f = tier_settings["frequency"]
    return {"daily": 1, "weekly": 7}.get(f["kind"], f.get("n", 1))


def _bid_path(bid: str) -> Path:
    try:
        return make_backup_engine().backup_path(bid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def _require_confirm(body: dict, word: str) -> None:
    if body.get("confirm") != word:
        raise HTTPException(status_code=422, detail=f'confirmation required: pass confirm:"{word}"')


@router.get("/backup/status", dependencies=[Depends(login_required)])
async def backup_status():
    store = make_backup_store()
    settings = await store.get_settings()
    eng = make_backup_engine()
    tiers = {}
    now = datetime.now().astimezone()
    for t in TIERS:
        last_ok = await store.last_run(t, "ok")
        last_err = await store.last_run(t, "error")
        stale = False
        if settings[t]["enabled"]:
            window = timedelta(days=2 * _interval_days(settings[t]))
            stale = (last_ok is None or
                     datetime.fromisoformat(last_ok["finished_at"]) < now - window)
        tiers[t] = {"enabled": settings[t]["enabled"], "last_ok": last_ok,
                    "last_error": (last_err or {}).get("error"),
                    "running": eng.running.get(t, False),
                    "next_run": next_fire(t), "stale": stale}
    master = live = None
    try:
        master = len([i for i in await make_config_store().applied() if i["kind"] == "model"])
    except Exception:
        pass
    try:
        live = len(await make_models_client().list_models())
    except Exception:
        pass
    return {"tiers": tiers, "master_models": master, "live_models": live,
            "master_empty_live_nonempty": bool(master == 0 and (live or 0) > 0)}


@router.get("/backup/settings", dependencies=[Depends(login_required)])
async def backup_settings_get():
    return await make_backup_store().get_settings()


@router.put("/backup/settings", dependencies=[Depends(login_required)])
async def backup_settings_put(body: dict = Body(...)):
    store = make_backup_store()
    try:
        for tier in TIERS:
            if tier in body:
                await store.save_settings(tier, validate_tier_settings(body[tier]))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    settings = await store.get_settings()
    try:
        write_mirror(get_settings().backup_dir, settings)
    except OSError as e:
        log.warning("could not write settings mirror: %s", e)
    sched = get_scheduler()
    if sched is not None:
        await register_backup_jobs(sched, store, make_backup_engine)
    return settings


@router.post("/backup/run", dependencies=[Depends(login_required)])
async def backup_run(body: dict = Body(...)):
    tier = body.get("tier")
    if tier not in TIERS:
        raise HTTPException(status_code=422, detail="tier must be config|logs")
    eng = make_backup_engine()
    out = await (eng.run_config() if tier == "config" else eng.run_logs())
    if not out.get("ok") and out.get("error") == "already running":
        raise HTTPException(status_code=409, detail="a backup for this tier is already running")
    return out


@router.get("/backup/list", dependencies=[Depends(login_required)])
async def backup_list():
    out = make_backup_engine().list_backups()
    out["runs"] = await make_backup_store().runs(limit=40)
    return out


def _load_source_items(bid: str) -> list[dict]:
    p = _bid_path(bid)
    if bid.startswith("config/"):
        p = p / "ui_config.json"
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"{bid}: ui_config.json not found")
    try:
        return parse_export(p.read_text())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/backup/rollback/preview", dependencies=[Depends(login_required)])
async def backup_rollback_preview(source: str):
    items = _load_source_items(source)
    current = await make_config_store().applied()
    out = rollback_preview(current, items)
    out["undecryptable"] = check_decryptable(items, _fernet())
    return out


@router.post("/backup/rollback", dependencies=[Depends(login_required)])
async def backup_rollback(body: dict = Body(...)):
    _require_confirm(body, "ROLLBACK")
    items = _load_source_items(body.get("source", ""))
    bad = check_decryptable(items, _fernet())
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"cannot decrypt with current secret: {', '.join(bad)}")
    s = get_settings()
    return await rollback_config(items, config_store=make_config_store(),
                                 models_client=make_models_client(), mcp_client=make_mcp_client(),
                                 reloader=make_reloader(), config_path=s.config_path,
                                 fernet=_fernet())


@router.post("/backup/recover", dependencies=[Depends(login_required)])
async def backup_recover(body: dict = Body(...)):
    _require_confirm(body, "RECOVER")
    bid = body.get("source", "")
    if not bid.startswith("config/"):
        raise HTTPException(status_code=422, detail="full recovery needs a config-tier backup")
    p = _bid_path(bid)
    if not p.is_dir():
        raise HTTPException(status_code=404, detail="backup not found")
    s = get_settings()
    import asyncpg
    from app.backup_engine import _default_run_subprocess
    out = await full_recovery(p, dsn=s.database_url, reloader=make_reloader(),
                              config_path=s.config_path,
                              connect=lambda: asyncpg.connect(s.database_url),
                              run_subprocess=_default_run_subprocess,
                              salt_key=None, fernet_secret=(s.credentials_key or s.session_secret))
    return out


@router.post("/backup/restore-logs", dependencies=[Depends(login_required)])
async def backup_restore_logs(body: dict = Body(...)):
    _require_confirm(body, "MERGE")
    src = body.get("source", "")
    eng = make_backup_engine()
    if src == "all":
        dirs = [eng.backup_path(b["id"]) for b in eng.list_backups()["backups"]
                if b["tier"] == "logs"]
        dirs.sort()
    else:
        if not src.startswith("logs/"):
            raise HTTPException(status_code=422, detail="restore-logs needs a logs-tier backup or 'all'")
        p = _bid_path(src)
        if not p.is_dir(): raise HTTPException(status_code=404, detail="backup not found")
        dirs = [p]
    import asyncpg
    s = get_settings()
    return await restore_logs(dirs, lambda: asyncpg.connect(s.database_url))


@router.get("/backup/download", dependencies=[Depends(login_required)])
async def backup_download(path: str):
    p = _bid_path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(p, filename=p.name)


@router.delete("/backup/item", dependencies=[Depends(login_required)])
async def backup_delete(body: dict = Body(...)):
    import shutil
    p = _bid_path(body.get("path", ""))
    if p.is_dir() and (p / "manifest.json").is_file():
        shutil.rmtree(p)
    elif p.is_file() and p.suffix == ".json":
        p.unlink()
    else:
        raise HTTPException(status_code=404, detail="not a deletable backup")
    return {"ok": True}
```

`ui/app/main.py`: import `backup_routes` alongside the other route modules and add `app.include_router(backup_routes.router)`.

- [ ] **Step 4: Run**: `cd ui && python -m pytest tests/test_backup_routes.py tests/ -q` → PASS
- [ ] **Step 5: Commit** `git add -A ui/app ui/tests && git commit -m "feat(backup): /api/backup routes (status, settings, run, list, restores, download)"`

---

### Task 10: Empty-master guard + per-Apply snapshot hook

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py` (resync + apply)
- Test: extend `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Consumes: `make_backup_engine` (import inside function to avoid cycles).
- Behavior (spec §7): in hybrid mode, when effective master has 0 non-deleted `model` items AND LiteLLM lists ≥1 model → `409` with the spec's message unless body `{"force": true}`. Snapshot: after a successful `apply_config`, best-effort `write_snapshot(await store.applied())`.

- [ ] **Step 1: Write the failing tests** (follow the file's existing `_client`/fake conventions — read `ui/tests/test_config_v3_routes.py` first and reuse its client builder; the assertions to add:)

```python
def test_resync_refuses_when_master_empty_but_live_has_models(tmp_path):
    # fake models client returns 2 live models; fake store returns no model items
    c = _hybrid_client(tmp_path, live_models=2, master_items=[])
    r = c.post("/api/config/resync", json={})
    assert r.status_code == 409 and "refusing" in r.json()["detail"]
    # force override proceeds
    assert c.post("/api/config/resync", json={"force": True}).status_code == 200


def test_apply_refuses_when_master_empty_but_live_has_models(tmp_path):
    c = _hybrid_client(tmp_path, live_models=2, master_items=[])
    assert c.post("/api/apply", json={}).status_code == 409


def test_apply_writes_snapshot_on_success(tmp_path):
    c = _hybrid_client(tmp_path, live_models=0, master_items=[_model_item()])
    c.post("/api/apply", json={})
    snaps = list((tmp_path / "backups" / "snapshots").glob("*-apply.json"))
    assert len(snaps) == 1
```

(The implementer writes `_hybrid_client` by copying the file's existing hybrid-mode client helper and parameterizing the fake `ModelsClient.list_models` result and the fake store's applied items; set env `BACKUP_DIR` to `tmp_path/"backups"` in the helper.)

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implementation** — in `config_v3_routes.py`:

Add a helper near the factories:

```python
async def _guard_empty_master(store, body: dict | None) -> None:
    """Spec §7: refuse mass-delete when the master is empty but LiteLLM serves models."""
    if body and body.get("force") is True:
        return
    eff = effective(await store.applied(), await store.staged())
    master_models = [i for i in eff if i["kind"] == "model" and i.get("flag") != "deleted"]
    if master_models:
        return
    try:
        live = await make_models_client().list_models()
    except Exception:
        return                      # can't see live: don't block on a probe failure
    if live:
        raise HTTPException(status_code=409, detail=(
            f"master config is empty but LiteLLM serves {len(live)} models — refusing to "
            f"delete them; restore from Backup & Restore, or pass force:true to wipe deliberately"))
```

`config_resync` gains `body: dict | None = Body(None)` and calls `await _guard_empty_master(store, body)` right after `store = make_config_store()` / before reconciling. `apply` gains `body: dict | None = Body(None)`; in hybrid mode (`s.store_model_in_db`) call `await _guard_empty_master(make_config_store(), body)` before `apply_config`. After a successful `apply_config(...)` result, add:

```python
        try:
            from app.routes.backup_routes import make_backup_engine
            make_backup_engine().write_snapshot(await make_config_store().applied())
        except Exception:
            import logging; logging.getLogger("uvicorn.error").warning(
                "apply snapshot failed", exc_info=True)
```

(wrap the existing `return await apply_config(...)` into `result = await apply_config(...)`, snapshot, `return result`).

- [ ] **Step 4: Run**: `cd ui && python -m pytest tests/test_config_v3_routes.py -q` → PASS (all pre-existing tests must stay green — the guard only fires on the empty-master+live combination)
- [ ] **Step 5: Commit** `git add -A ui/app ui/tests && git commit -m "feat(guard): refuse empty-master resync/apply; snapshot master on every apply"`

---

### Task 11: Request/response bodies in transaction detail

**Files:**
- Modify: `ui/app/routes/usage_routes.py` (`usage_tx` SELECT + `_shape_tx`)
- Test: extend `ui/tests/test_usage_routes.py`

- [ ] **Step 1: Write the failing test** (reuse the file's existing fake-row helpers/conventions):

```python
def test_shape_tx_includes_parsed_bodies():
    from app.routes.usage_routes import _shape_tx
    r = _base_tx_row()          # the file's existing helper for a fake DB row dict
    r["proxy_server_request"] = json.dumps({"messages": [{"role": "user", "content": "hi"}]})
    r["response"] = json.dumps({"choices": [{"message": {"role": "assistant", "content": "yo"}}]})
    out = _shape_tx(r)
    assert out["request"]["messages"][0]["content"] == "hi"
    assert out["response"]["choices"][0]["message"]["content"] == "yo"


def test_shape_tx_bodies_absent_or_empty_are_none():
    from app.routes.usage_routes import _shape_tx
    r = _base_tx_row(); r["proxy_server_request"] = "{}"; r["response"] = None
    out = _shape_tx(r)
    assert out["request"] is None and out["response"] is None
```

(If the test file has no `_base_tx_row` helper, add one returning the minimal dict `_shape_tx` needs — copy the field set from an existing `_shape_tx` test.)

- [ ] **Step 2: Run** → FAIL (KeyError `request`)

- [ ] **Step 3: Implementation** — in `usage_routes.py` add:

```python
def _parse_body(v):
    """jsonb arrives as str from asyncpg; '{}'/empty → None (not captured)."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return None
    return v if isinstance(v, (dict, list)) and v else None
```

In `usage_tx`'s SELECT, after `l.metadata` add `, l.proxy_server_request, l.response`. In `_shape_tx`'s returned dict add:

```python
            "request": _parse_body(r.get("proxy_server_request")),
            "response": _parse_body(r.get("response")),
```

- [ ] **Step 4: Run**: `cd ui && python -m pytest tests/test_usage_routes.py -q` → PASS
- [ ] **Step 5: Commit** `git add -A ui/app ui/tests && git commit -m "feat(logging): expose request/response bodies in transaction detail API"`

---

### Task 12: Frontend — api.js + Backup & Restore page

**Files:**
- Modify: `ui/frontend/src/lib/api.js`
- Create: `ui/frontend/src/routes/BackupRestore.svelte`

**Interfaces:**
- Consumes: Task 9 routes. Produces: `<BackupRestore />` (no props) used by Task 13's Settings tab; api methods `backupStatus, backupSettings, saveBackupSettings, backupRun, backupList, rollbackPreview, backupRollback, backupRecover, backupRestoreLogs, backupDelete, backupDownloadUrl`.

- [ ] **Step 1: Add api.js methods** (before the closing `}` of `export const api`):

```javascript
  backupStatus: () => req('/api/backup/status'),
  backupSettings: () => req('/api/backup/settings'),
  saveBackupSettings: (body) => req('/api/backup/settings', { method: 'PUT', body: JSON.stringify(body) }),
  backupRun: (tier) => req('/api/backup/run', { method: 'POST', body: JSON.stringify({ tier }) }),
  backupList: () => req('/api/backup/list'),
  rollbackPreview: (source) => req(`/api/backup/rollback/preview?source=${encodeURIComponent(source)}`),
  backupRollback: (source) => req('/api/backup/rollback', { method: 'POST', body: JSON.stringify({ source, confirm: 'ROLLBACK' }) }),
  backupRecover: (source) => req('/api/backup/recover', { method: 'POST', body: JSON.stringify({ source, confirm: 'RECOVER' }) }),
  backupRestoreLogs: (source) => req('/api/backup/restore-logs', { method: 'POST', body: JSON.stringify({ source, confirm: 'MERGE' }) }),
  backupDelete: (path) => req('/api/backup/item', { method: 'DELETE', body: JSON.stringify({ path }) }),
  backupDownloadUrl: (path) => `/api/backup/download?path=${encodeURIComponent(path)}`,
```

- [ ] **Step 2: Create `BackupRestore.svelte`** (Svelte 5 runes; typed-confirmation via `prompt()`; card/table styles copied from `Housekeeping.svelte`):

```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'

  let status = $state(null), list = $state(null), settings = $state(null)
  let err = $state(''), msg = $state(''), busyTier = $state(''), restoring = $state(false)
  let recoverySteps = $state(null), mergeResult = $state(null), preview = $state(null)
  const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

  async function load() {
    err = ''
    try {
      [status, list, settings] = await Promise.all([api.backupStatus(), api.backupList(), api.backupSettings()])
    } catch (e) { err = e.message }
  }
  onMount(load)

  function fmtBytes(n) {
    if (n == null) return '—'
    if (n < 1024) return `${n} B`
    if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
    if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`
    return `${(n / 1073741824).toFixed(2)} GB`
  }
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString() : '—'

  async function saveTier(tier) {
    err = ''; msg = ''
    try {
      settings = await api.saveBackupSettings({ [tier]: settings[tier] })
      msg = `${tier} schedule saved`
      status = await api.backupStatus()
    } catch (e) { err = e.message }
  }

  async function runNow(tier) {
    busyTier = tier; err = ''; msg = ''
    try {
      const r = await api.backupRun(tier)
      msg = r.ok ? `Backed up → ${r.path} (${fmtBytes(r.bytes)})` : `Backup failed: ${r.error}`
      await load()
    } catch (e) { err = e.message } finally { busyTier = '' }
  }

  async function doRollback(id) {
    err = ''; preview = null
    try { preview = { id, ...(await api.rollbackPreview(id)) } } catch (e) { err = e.message }
  }
  async function confirmRollback() {
    if (prompt(`Replace the current master config with ${preview.id}?\nStaged changes are discarded.\nType ROLLBACK to confirm`) !== 'ROLLBACK') return
    restoring = true; err = ''; msg = ''
    try {
      const r = await api.backupRollback(preview.id)
      msg = `Rolled back — models: +${r.models?.added ?? 0}/~${r.models?.updated ?? 0}/−${r.models?.deleted ?? 0}, restart: ${r.restart}`
      preview = null; await load()
    } catch (e) { err = e.message } finally { restoring = false }
  }

  async function doRecover(id) {
    if (prompt(`FULL RECOVERY from ${id}?\nStops the proxy ~1 min and replaces config, models, keys and teams.\nUsage logs are untouched.\nType RECOVER to confirm`) !== 'RECOVER') return
    restoring = true; err = ''; msg = ''; recoverySteps = null
    try {
      const r = await api.backupRecover(id)
      recoverySteps = r.steps
      msg = r.ok ? 'Full recovery complete' : 'Full recovery finished with errors — see steps'
      await load()
    } catch (e) { err = e.message } finally { restoring = false }
  }

  async function doMerge(id) {
    if (prompt(`Merge usage rows from ${id === 'all' ? 'ALL logs backups' : id} into the database?\nExisting rows are never modified.\nType MERGE to confirm`) !== 'MERGE') return
    restoring = true; err = ''; msg = ''; mergeResult = null
    try { mergeResult = await api.backupRestoreLogs(id); await load() }
    catch (e) { err = e.message } finally { restoring = false }
  }

  async function doDelete(id) {
    if (!confirm(`Delete ${id}? This cannot be undone.`)) return
    try { await api.backupDelete(id); await load() } catch (e) { err = e.message }
  }
</script>

<div class="wrap">
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if msg}<div class="banner ok">{msg}</div>{/if}
  {#if status?.master_empty_live_nonempty}
    <div class="banner err">Master config is empty while LiteLLM serves {status.live_models} models —
      this usually means the ui_* tables were lost. Restore a config backup below; do NOT Resync.</div>
  {/if}

  {#if status}
    <div class="card"><h2>Status</h2>
      <table><thead><tr><th>Tier</th><th>Last good backup</th><th>Next run</th><th>State</th><th></th></tr></thead><tbody>
        {#each ['config', 'logs'] as t}
          {@const s = status.tiers[t]}
          <tr>
            <td>{t}</td>
            <td>{s.last_ok ? `${fmtTime(s.last_ok.finished_at)} · ${fmtBytes(s.last_ok.bytes)}` : 'never'}</td>
            <td>{s.enabled ? fmtTime(s.next_run) : 'disabled'}</td>
            <td>{s.running ? 'running…' : s.stale ? '⚠ stale' : s.last_error ? `⚠ last error: ${s.last_error}` : 'ok'}</td>
            <td><button onclick={() => runNow(t)} disabled={busyTier === t}>{busyTier === t ? 'Backing up…' : 'Back up now'}</button></td>
          </tr>
        {/each}
      </tbody></table>
    </div>
  {/if}

  {#if settings}
    {#each ['config', 'logs'] as t}
      <div class="card"><h2>{t === 'config' ? 'Config backups' : 'Logs backups (usage export)'}</h2>
        <p class="hint">{t === 'config'
          ? 'Full dump of configuration, models, MCP servers, keys and teams (usage tables excluded).'
          : 'Incremental CSV slices of usage tables — with request logging on, these carry full request/response bodies (your dataset export). Retention 0 keeps slices forever.'}</p>
        <div class="row">
          <label class="chk"><input type="checkbox" bind:checked={settings[t].enabled} /> Enabled</label>
          <select bind:value={settings[t].frequency.kind}>
            <option value="daily">Daily</option><option value="weekly">Weekly</option>
            <option value="every_n_days">Every N days</option>
          </select>
          {#if settings[t].frequency.kind === 'weekly'}
            <select bind:value={settings[t].frequency.weekday}>
              {#each WEEKDAYS as d, i}<option value={i}>{d}</option>{/each}
            </select>
          {/if}
          {#if settings[t].frequency.kind === 'every_n_days'}
            <label>N <input class="num" type="number" min="2" max="365" bind:value={settings[t].frequency.n} /></label>
          {/if}
          <label>at <input class="hhmm" type="time" bind:value={settings[t].time} /></label>
          <label>retain <input class="num" type="number" min="0" max="3650" bind:value={settings[t].retention_days} /> days</label>
          <button onclick={() => saveTier(t)}>Save</button>
        </div>
      </div>
    {/each}
  {/if}

  {#if list}
    <div class="card"><h2>Backups</h2>
      {#if !list.backups.length}<p class="hint">No backups yet.</p>{:else}
      <table><thead><tr><th>Backup</th><th>Taken</th><th>Size</th><th>Contents</th><th>Actions</th></tr></thead><tbody>
        {#each list.backups as b}
          <tr>
            <td class="mono">{b.id}</td><td>{fmtTime(b.taken_at)}</td><td>{fmtBytes(b.bytes)}</td>
            <td>{Object.entries(b.summary || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}</td>
            <td class="actions">
              {#if b.tier === 'config'}
                <button onclick={() => doRollback(b.id)} disabled={restoring}>Rollback config</button>
                <button class="danger" onclick={() => doRecover(b.id)} disabled={restoring}>Full recovery</button>
              {:else}
                <button onclick={() => doMerge(b.id)} disabled={restoring}>Restore (merge)</button>
              {/if}
              {#each b.files.filter(f => f !== 'manifest.json') as f}
                <a class="dl" href={api.backupDownloadUrl(`${b.id}/${f}`)} download title={f}>⬇ {f}</a>
              {/each}
              <button class="danger" onclick={() => doDelete(b.id)} disabled={restoring}>Delete</button>
            </td>
          </tr>
        {/each}
      </tbody></table>
      {#if list.backups.some(b => b.tier === 'logs')}
        <button onclick={() => doMerge('all')} disabled={restoring}>Restore ALL logs slices (merge)</button>
      {/if}
      {/if}
    </div>

    <div class="card"><h2>Apply snapshots</h2>
      <p class="hint">The master config is snapshotted after every successful Apply (last 50 kept).</p>
      {#if !list.snapshots.length}<p class="hint">No snapshots yet.</p>{:else}
      <table><tbody>
        {#each list.snapshots as s}
          <tr><td class="mono">{s.id}</td><td>{fmtBytes(s.bytes)}</td>
            <td><button onclick={() => doRollback(s.id)} disabled={restoring}>Rollback config</button>
                <button class="danger" onclick={() => doDelete(s.id)} disabled={restoring}>Delete</button></td></tr>
        {/each}
      </tbody></table>
      {/if}
    </div>

    <div class="card"><h2>Run history</h2>
      {#if !(list.runs || []).length}<p class="hint">No runs yet.</p>{:else}
      <table><thead><tr><th>Tier</th><th>Started</th><th>Status</th><th>Result</th></tr></thead><tbody>
        {#each list.runs as r}
          <tr><td>{r.tier}</td><td>{fmtTime(r.started_at)}</td>
            <td class:red={r.status === 'error'}>{r.status}</td>
            <td>{r.error || (r.path ? `${r.path} · ${fmtBytes(r.bytes)}` : '—')}</td></tr>
        {/each}
      </tbody></table>
      {/if}
    </div>
  {/if}

  {#if preview}
    <div class="card"><h2>Rollback preview — {preview.id}</h2>
      {#if preview.undecryptable?.length}
        <div class="banner err">Cannot decrypt with the current secret: {preview.undecryptable.join(', ')} — rollback refused.</div>
      {:else}
        <p>+{preview.added.length} added · −{preview.removed.length} removed · ~{preview.changed.length} changed
          {#if preview.restart_kinds_changed} · includes settings → proxy restart (~25s){/if}</p>
        <ul class="diff">
          {#each preview.added as i}<li class="green">+ {i.kind}/{i.name}</li>{/each}
          {#each preview.removed as i}<li class="red">− {i.kind}/{i.name}</li>{/each}
          {#each preview.changed as i}<li>~ {i.kind}/{i.name}</li>{/each}
        </ul>
        <button class="danger" onclick={confirmRollback} disabled={restoring}>{restoring ? 'Rolling back…' : 'Roll back to this'}</button>
      {/if}
      <button onclick={() => preview = null}>Close</button>
    </div>
  {/if}

  {#if recoverySteps}
    <div class="card"><h2>Recovery steps</h2>
      <table><tbody>{#each recoverySteps as s}
        <tr><td>{s.step}</td><td class:red={s.status === 'error'} class:green={s.status === 'ok'}>{s.status}</td><td>{s.detail || ''}</td></tr>
      {/each}</tbody></table>
    </div>
  {/if}

  {#if mergeResult}
    <div class="card"><h2>Logs merge result</h2>
      <table><thead><tr><th>Table</th><th>Inserted</th><th>Skipped (already present)</th><th>Notes</th></tr></thead><tbody>
        {#each Object.entries(mergeResult.tables) as [t, v]}
          <tr><td class="mono">{t}</td><td>{v.inserted}</td><td>{v.skipped}</td>
            <td>{v.error || (v.dropped_columns?.length ? `dropped: ${v.dropped_columns.join(', ')}` : '')}</td></tr>
        {/each}
      </tbody></table>
    </div>
  {/if}
</div>

<style>
  .wrap{max-width:900px}h2{font-size:15px;margin:0 0 10px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card)}
  table{width:100%;border-collapse:collapse}th{text-align:left;font-size:12px;color:var(--muted);padding:6px 8px}
  td{padding:6px 8px;border-bottom:1px solid var(--border);font-size:13px;vertical-align:top}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .chk{display:flex;align-items:center;gap:6px}
  .mono{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:12px}
  .num{width:64px}.hhmm{width:110px}
  input,select{padding:6px 8px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font:inherit}
  button{padding:7px 11px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  button.danger{color:#ff3b30;border-color:#ffd0cc}button:disabled{opacity:.5}
  .dl{font-size:12px;margin-right:6px;text-decoration:none;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:2px 6px}
  .actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px;margin-top:10px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}
  .hint{font-size:12px;color:var(--muted)}
  .diff{font-family:ui-monospace,monospace;font-size:12px;max-height:220px;overflow:auto;padding-left:16px}
  .green{color:#1d7a33}.red{color:#c0271d}td.red{color:#c0271d}td.green{color:#1d7a33}
</style>
```

- [ ] **Step 3: Build check**: `cd ui/frontend && npm run build` → succeeds
- [ ] **Step 4: Commit** `git add ui/frontend/src/lib/api.js ui/frontend/src/routes/BackupRestore.svelte && git commit -m "feat(ui): Backup & Restore page + api methods"`

---

### Task 13: Settings tabs + Request-logging card

**Files:**
- Modify: `ui/frontend/src/routes/Settings.svelte`

**Interfaces:** Consumes `<BackupRestore />` (Task 12) and the existing `store.itemsOfKind` / `store.stageItem` used by the health-check card.

- [ ] **Step 1: Restructure into tabs.** In the `<script>` add:

```javascript
  import BackupRestore from './BackupRestore.svelte'
  let tab = $state('general')

  // Request & response logging (general_setting store_prompts_in_spend_logs)
  let logMsg = $state(''), logErr = $state(''), logBusy = $state(false)
  let logEnabled = $state(false)
  function loadLogSetting() {
    const it = store.itemsOfKind('general_setting').find(i => i.name === 'store_prompts_in_spend_logs')
    logEnabled = it ? it.data === true : false
  }
  async function toggleLogging(v) {
    logBusy = true; logMsg = ''; logErr = ''
    try {
      await store.stageItem('general_setting', 'store_prompts_in_spend_logs', v)
      logEnabled = v
      logMsg = 'Staged. Click Apply to make it live (settings change → brief proxy restart ~25s).'
    } catch (e) { logErr = e.message } finally { logBusy = false }
  }
```

Call `loadLogSetting()` inside the existing `onMount` callback (after `loadHcInterval()`).

- [ ] **Step 2: Template.** Right under `<h1>Settings</h1>` add the tab bar, wrap ALL existing cards (Appearance … Change admin password, unchanged) in the `general` branch, and add the logging card at the end of that branch:

```svelte
  <div class="tabs">
    <button class:active={tab === 'general'} onclick={() => tab = 'general'}>General</button>
    <button class:active={tab === 'backup'} onclick={() => tab = 'backup'}>Backup &amp; Restore</button>
  </div>
  {#if tab === 'general'}
    <!-- existing cards, byte-for-byte unchanged, PLUS: -->
    <div class="card"><h2>Request &amp; response logging</h2>
      <p class="hint">When enabled, LiteLLM stores every request body (messages, tools, params) and the
        full response on each usage row (<code>LiteLLM_SpendLogs</code>). Nothing is truncated
        (<code>MAX_STRING_LENGTH_PROMPT_IN_DB</code> is raised in docker-compose). Review bodies in
        Usage → Activity; export them via the <strong>logs backup tier</strong> (Backup &amp; Restore) —
        enable it so bodies are archived before housekeeping prunes rows.
        Privacy note: mail-scanning and memory keys will store message content; a key can opt out via
        key metadata <code>turn_off_message_logging: true</code>.</p>
      <label class="row"><input type="checkbox" checked={logEnabled} disabled={logBusy}
        onchange={(e) => toggleLogging(e.target.checked)} /> Store request &amp; response bodies</label>
      {#if logErr}<div class="banner err">{logErr}</div>{/if}
      {#if logMsg}<div class="banner ok">{logMsg}</div>{/if}
    </div>
  {:else}
    <BackupRestore />
  {/if}
```

Add to the `<style>` block:

```css
  .tabs{display:flex;gap:6px;margin-top:10px}
  .tabs button{padding:7px 14px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  .tabs button.active{background:#0a84ff;color:#fff;border-color:#0a84ff}
```

Also widen the page: change `.page{...max-width:680px}` to `max-width:900px`.

- [ ] **Step 3: Build**: `cd ui/frontend && npm run build` → succeeds
- [ ] **Step 4: Commit** `git commit -am "feat(ui): Settings tabs (General | Backup & Restore) + request-logging toggle"`

---

### Task 14: App banner + Activity transcript viewer

**Files:**
- Modify: `ui/frontend/src/App.svelte`, `ui/frontend/src/routes/ActivityFeed.svelte`

- [ ] **Step 1: App banner.** In `App.svelte` script:

```javascript
  let backupAlert = $state(null)
  async function checkBackup() {
    try {
      const s = await api.backupStatus()
      if (s.master_empty_live_nonempty)
        backupAlert = { level: 'err', text: `Master config is empty while LiteLLM serves ${s.live_models} models — restore from Settings → Backup & Restore. Do NOT Resync.` }
      else {
        const stale = Object.entries(s.tiers).filter(([, t]) => t.stale).map(([k]) => k)
        const failed = Object.entries(s.tiers).filter(([, t]) => t.last_error && !t.stale).map(([k]) => k)
        if (stale.length) backupAlert = { level: 'warn', text: `Backups stale: ${stale.join(', ')} — check Settings → Backup & Restore.` }
        else if (failed.length) backupAlert = { level: 'warn', text: `Last ${failed.join(', ')} backup failed — check Settings → Backup & Restore.` }
        else backupAlert = null
      }
    } catch { /* status is best-effort */ }
  }
```

Call `checkBackup()` from `onMount` (after `store.load()`) and from `onLogin`. In the template, right before the `{#if store.pending}` applybar:

```svelte
      {#if backupAlert}
        <div class="banner {backupAlert.level === 'err' ? 'err' : 'warn'}">
          {backupAlert.text}
          <button class="dismiss" onclick={() => backupAlert = null}>✕</button>
        </div>
      {/if}
```

Add styles (App.svelte already defines `.banner.ok/.err` — add):

```css
  .banner.warn{background:#fff6e5;color:#8a5a00}
  .dismiss{float:right;border:0;background:none;cursor:pointer;color:inherit;font:inherit}
```

(If `.banner`/`.banner.err` are not in App.svelte's styles, copy the `.banner` rules from `Housekeeping.svelte`.)

- [ ] **Step 2: ActivityFeed transcript.** In the script add pure helpers:

```javascript
  function txMessages(request) {
    const msgs = request?.messages
    if (!Array.isArray(msgs)) return null
    return msgs.map(m => ({
      role: m.role || '?',
      content: typeof m.content === 'string' ? m.content
        : Array.isArray(m.content) ? m.content.map(p => p.text ?? '').join('\n')
        : m.content == null ? '' : JSON.stringify(m.content),
      tool_calls: m.tool_calls,
    }))
  }
  function txResponseText(response) {
    const msg = response?.choices?.[0]?.message
    if (msg) return { role: msg.role || 'assistant', content: msg.content ?? '', tool_calls: msg.tool_calls }
    if (typeof response?.content === 'string') return { role: 'assistant', content: response.content }
    return null
  }
  let rawBodies = $state({})   // id → bool
```

In the detail pane, after the error box `{#if t.error}…{/if}` block, add:

```svelte
                  {#if t.request || t.response}
                    <div class="bodybox">
                      <div class="bodyhead">Request / response
                        <button class="linkbtn" onclick={() => rawBodies = { ...rawBodies, [r.id]: !rawBodies[r.id] }}>
                          {rawBodies[r.id] ? 'transcript' : 'raw JSON'}</button>
                      </div>
                      {#if rawBodies[r.id]}
                        {#if t.request}<details open><summary>Request JSON</summary><pre>{JSON.stringify(t.request, null, 2)}</pre></details>{/if}
                        {#if t.response}<details open><summary>Response JSON</summary><pre>{JSON.stringify(t.response, null, 2)}</pre></details>{/if}
                      {:else}
                        {#each (txMessages(t.request) || []) as m}
                          <div class="msg"><span class="role role-{m.role}">{m.role}</span>
                            <pre class="msgtext">{m.content}</pre>
                            {#if m.tool_calls}<pre class="msgtext tool">{JSON.stringify(m.tool_calls, null, 2)}</pre>{/if}
                          </div>
                        {/each}
                        {#if txResponseText(t.response)}
                          {@const rr = txResponseText(t.response)}
                          <div class="msg resp"><span class="role role-assistant">{rr.role} ⤶</span>
                            <pre class="msgtext">{rr.content}</pre>
                            {#if rr.tool_calls}<pre class="msgtext tool">{JSON.stringify(rr.tool_calls, null, 2)}</pre>{/if}
                          </div>
                        {/if}
                        {#if !txMessages(t.request) && !txResponseText(t.response)}
                          <p class="empty">Bodies present but in an unrecognized shape — use raw JSON.</p>
                        {/if}
                      {/if}
                    </div>
                  {:else}
                    <p class="empty">Request/response not captured (enable it in Settings → Request &amp; response logging).</p>
                  {/if}
```

Styles (append to the component's `<style>`):

```css
  .bodybox{margin-top:10px;border:1px solid var(--border);border-radius:8px;padding:10px}
  .bodyhead{font-size:12px;color:var(--muted);margin-bottom:6px}
  .msg{margin:6px 0}.msg.resp{border-top:1px dashed var(--border);padding-top:6px}
  .role{font-size:11px;font-weight:600;text-transform:uppercase;color:var(--muted)}
  .msgtext{white-space:pre-wrap;word-break:break-word;font-size:12px;margin:2px 0 0;max-height:320px;overflow:auto}
  .msgtext.tool{color:var(--muted)}
```

- [ ] **Step 3: Build**: `cd ui/frontend && npm run build` → succeeds
- [ ] **Step 4: Commit** `git commit -am "feat(ui): backup alert banner + request/response transcript in activity detail"`

---

### Task 15: Dockerfile, compose, env, docs

**Files:**
- Modify: `ui/Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, `docs/admin-ui-guide.md`

- [ ] **Step 1: Dockerfile** — in the runtime stage, before `COPY pyproject.toml ./`:

```dockerfile
# pg_dump/pg_restore 16 for the Backup & Restore feature. Debian bookworm ships
# client v15 which refuses a v16 server, so install from the PGDG repo.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: docker-compose.yml** — `llm-proxy-ui` service: add to `environment:` `BACKUP_DIR: /backups` and `TZ: ${TZ:-UTC}`; add to `volumes:` `- ./backups:/backups   # scheduled backups (Settings → Backup & Restore)`. `litellm` service: add after `DISABLE_AIOHTTP_TRANSPORT`:

```yaml
      # Request/response logging (Settings → Request & response logging) stores
      # bodies in SpendLogs; LiteLLM truncates every string to 2048 chars by
      # default — 10M chars = effectively no truncation (full-fidelity dataset).
      MAX_STRING_LENGTH_PROMPT_IN_DB: "10000000"
```

- [ ] **Step 3: `.env.example`** — append:

```bash
# Local timezone for schedules and displayed times (backup schedules fire in
# this TZ). E.g. Australia/Sydney. Empty = UTC.
TZ=
```

- [ ] **Step 4: Docs.** README: after the pg_dump advice note (added 2026-08-24), append one sentence: `The UI also automates this: **Settings → Backup & Restore** schedules config and usage-log backups to ./backups with retention and one-click restore.` `docs/admin-ui-guide.md`: add a `## Backup & Restore` section covering (write real prose, ~30 lines): the two tiers and defaults, per-Apply snapshots, the three restores and their confirmation words, the empty-master banner/guard, the same-secrets caveat (`LITELLM_SALT_KEY`, `SESSION_SECRET`/`CREDENTIALS_KEY` must match the backup's fingerprints), request-logging toggle + where bodies appear + per-key opt-out (`metadata.turn_off_message_logging: true`), and that logs slices double as the dataset export.

- [ ] **Step 5: Verify**: `docker compose config >/dev/null` (from repo root) exits 0; `cd ui && python -m pytest tests/ -q` and `cd ui/frontend && npm run build` still pass.
- [ ] **Step 6: Commit** `git add -A && git commit -m "feat(backup): pg client 16 in image; backups mount, TZ, no-truncation env; docs"`

---

### Task 16: Live proof on the dev stack (no code changes expected)

**Files:** none committed (report only; fixes discovered here become normal fix commits).

Run the dev stack (`docker-compose.override.yml` with `build: ./ui`, `STORE_MODEL_IN_DB=true`, ports on 0.0.0.0; UI at `http://10.0.20.85:8081`, admin password from the local `.env`) and prove, in order:

- [ ] 1. **Schedule fires**: set config tier to daily at (now+2 min), Save; verify the run appears in history, `backups/config/<stamp>/` has all 4 files, dirs `0700`/files `0600`, and pruning leaves newer dirs alone.
- [ ] 2. **Snapshot + rollback**: change a model's cost via the UI, Apply → a `snapshots/*-apply.json` appears; Rollback to the pre-change snapshot via preview → drift shows `in_sync: true` and the cost is back.
- [ ] 3. **Incident simulation**: `docker exec <postgres> psql -c 'DROP TABLE ui_config_applied, ui_config_staged'` → UI shows the red banner; Resync returns 409; **Full recovery** from the step-1 backup → step log all ok, `/v1/models` count and key list match the manifest; drift `in_sync`.
- [ ] 4. **Request logging e2e**: enable the Settings toggle, Apply (restart), make a chat call through the proxy, open the Activity detail → transcript shows the prompt and response; raw JSON toggle works.
- [ ] 5. **Logs tier**: run logs backup now → slice contains the SpendLogs row incl. bodies; delete that row via psql; Restore (merge) → row is back, second merge inserts 0 / skips all; download link streams the csv.gz.
- [ ] 6. **Playwright walkthrough** of the Backup & Restore tab (screenshots for the PR/report).

Record outcomes (commands + observed outputs) in the task report.

---

## Self-review (done while writing)

- Spec coverage: §2→T1, §3-4→T3/T4, §5→T2/T5, §6.1→T6, §6.2→T7, §6.3→T8, §7→T9(status)+T10(guard)+T14(banner), §8→T13(toggle)+T11/T14(review)+T4(export)+T9(download)+T15(env), §9→T9, §10→T12/T13/T14, §11 woven through T3/T4/T9, §12→per-task tests + T16, §13→T15 (deploy itself happens post-merge, as usual).
- Type consistency: `BackupEngine` ctor/params match between T4/T5-placeholder/T9; `backup_path` id grammar shared via `_ID_RE`; `run_config/run_logs` return `{"ok", "path", "bytes", ...}` consumed by routes and UI; restore return shapes match the Svelte renderers; `TIERS` imported from `backup_store` everywhere.
- Known judgment calls for implementers: `_prune` tolerates stores without `get_settings` (fakes skip pruning); Task 8's dropped-column CSV rewrite block replaces the plain `copy_to_table` call; Task 10's tests adapt the existing `test_config_v3_routes.py` client helper rather than inventing a new harness.
- Fixed during self-review: `_prune` leftover line removed; logs watermark upper bound converted to naive **UTC** (SpendLogs stores UTC wall-clock); `BackupEngine.running` property added (status route relies on it).
