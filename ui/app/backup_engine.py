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


# append to ui/app/backup_engine.py
import asyncio
import gzip
import re
import shutil
import subprocess
from datetime import timedelta

import asyncpg

from app.backup_tables import (classify, base_tables, WATERMARK_COLUMNS,
                               ROLLING_DATE_COLUMN, ROLLING_WINDOW_DAYS, WATERMARK_GUARD_S,
                               USAGE_EXACT, TRANSIENT)

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
        # Path.mkdir(mode=..., parents=True) only applies `mode` to the leaf —
        # intermediate directories (the backup root, the tier dir) are created with
        # default, umask-derived permissions. chmod each level explicitly instead;
        # chmod (unlike mkdir's mode=) isn't subject to the process umask.
        d = self._dir
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
        for part in parts:
            d = d / part
            d.mkdir(mode=0o700, exist_ok=True)
            d.chmod(0o700)
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
                # Union live-classified usage/transient tables (catches prefix-matched
                # LiteLLM_Daily* aggregates) with the static definitions, so known
                # usage/transient tables are always excluded even if information_schema
                # introspection hasn't caught up yet (pg_dump ignores unmatched excludes).
                exclude = sorted(set(tiers["usage"]) | set(tiers["transient"]) | USAGE_EXACT | TRANSIENT)
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
                                async def sink(data: bytes): gz.write(data)
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
