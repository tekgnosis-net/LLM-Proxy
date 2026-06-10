from __future__ import annotations
import asyncpg
from fastapi import HTTPException
from app.auth import verify_password, hash_password
from app.settings import get_settings

_DDL = """CREATE TABLE IF NOT EXISTS ui_admin_auth (
  id int PRIMARY KEY DEFAULT 1,
  password_hash text NOT NULL,
  updated_at timestamptz DEFAULT now(),
  CONSTRAINT ui_admin_auth_single_row CHECK (id = 1))"""


def verify_and_hash(old: str, new: str, eff: str) -> str:
    """Pure: verify `old` against the effective hash, enforce a min length on `new`,
    return a fresh argon2 hash of `new`. Raises 401 (bad old) / 422 (weak new)."""
    if not verify_password(old, eff):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    if len(new or "") < 8:
        raise HTTPException(status_code=422, detail="new password must be at least 8 characters")
    return hash_password(new)


async def effective_hash() -> str:
    """The admin hash in effect: the DB override if set, else the env ADMIN_PASSWORD_HASH."""
    s = get_settings()
    if not s.database_url:
        return s.admin_password_hash
    conn = await asyncpg.connect(s.database_url)
    try:
        await conn.execute(_DDL)
        row = await conn.fetchrow("SELECT password_hash FROM ui_admin_auth WHERE id = 1")
    finally:
        await conn.close()
    return row["password_hash"] if row else s.admin_password_hash


async def set_hash(h: str) -> None:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(_DDL)
        await conn.execute(
            "INSERT INTO ui_admin_auth (id, password_hash, updated_at) VALUES (1, $1, now()) "
            "ON CONFLICT (id) DO UPDATE SET password_hash = $1, updated_at = now()", h)
    finally:
        await conn.close()
