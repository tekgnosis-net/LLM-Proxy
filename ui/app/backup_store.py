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


def _ensure_dir_0700(p: Path) -> None:
    """mkdir -p semantics, but chmod(0o700) every level actually created here.
    Path.mkdir(mode=...) only applies `mode` to the leaf; intermediate directories
    created implicitly via parents=True get default, umask-derived permissions —
    and mkdir's mode itself is umask-masked, whereas chmod is not. Never touches
    an already-existing ancestor (e.g. the caller's tmp_path)."""
    if p.exists():
        return
    _ensure_dir_0700(p.parent)
    p.mkdir(mode=0o700, exist_ok=True)
    p.chmod(0o700)


def write_mirror(backup_dir: Path | str, settings: dict) -> None:
    d = Path(backup_dir); _ensure_dir_0700(d)
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
