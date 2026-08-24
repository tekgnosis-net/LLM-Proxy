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
