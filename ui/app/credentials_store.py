from __future__ import annotations
import base64, hashlib
import asyncpg
from cryptography.fernet import Fernet
from typing import Any


def fernet_from_secret(secret: str) -> Fernet:
    if not secret:
        raise ValueError("credentials encryption secret must not be empty")  # no weak default
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def materialize_credentials(config: dict, decrypted: list[dict]) -> dict:
    """Pure: inject a credential_list (literal values) built from the decrypted vault entries.
    Empty vault → drop credential_list. Does not mutate the input."""
    out = {k: v for k, v in config.items() if k != "credential_list"}
    if decrypted:
        out["credential_list"] = [
            {"credential_name": c["credential_name"],
             "credential_values": {"api_key": c["api_key"]},
             "credential_info": {"provider": c.get("provider")}}
            for c in decrypted
        ]
    return out


class CredentialsStore:
    def __init__(self, dsn: str, fernet: Fernet):
        self._dsn = dsn; self._f = fernet

    async def _conn(self): return await asyncpg.connect(self._dsn)

    async def ensure_schema(self, conn) -> None:
        await conn.execute('''CREATE TABLE IF NOT EXISTS ui_credentials (
            credential_name text PRIMARY KEY, provider text,
            value_encrypted text NOT NULL, created_at timestamptz default now())''')

    async def create(self, name: str, provider: str, api_key: str) -> None:
        enc = self._f.encrypt(api_key.encode()).decode()
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            await conn.execute('''INSERT INTO ui_credentials(credential_name,provider,value_encrypted)
                VALUES($1,$2,$3) ON CONFLICT(credential_name) DO UPDATE SET provider=$2,value_encrypted=$3''',
                name, provider, enc)
        finally: await conn.close()

    async def list_masked(self) -> list[dict[str, Any]]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            rows = await conn.fetch("SELECT credential_name, provider, created_at FROM ui_credentials ORDER BY credential_name")
            return [{"credential_name": r["credential_name"], "provider": r["provider"]} for r in rows]
        finally: await conn.close()

    async def list_decrypted(self) -> list[dict[str, Any]]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            rows = await conn.fetch("SELECT credential_name, provider, value_encrypted FROM ui_credentials ORDER BY credential_name")
            return [{"credential_name": r["credential_name"], "provider": r["provider"],
                     "api_key": self._f.decrypt(r["value_encrypted"].encode()).decode()} for r in rows]
        finally: await conn.close()

    async def delete(self, name: str) -> None:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            await conn.execute("DELETE FROM ui_credentials WHERE credential_name=$1", name)
        finally: await conn.close()
