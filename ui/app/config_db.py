from __future__ import annotations
import json
import asyncpg
from typing import Optional

APPLIED, STAGED = "ui_config_applied", "ui_config_staged"


def decide_flag(applied_has: bool) -> str:
    return "changed" if applied_has else "new"


class ConfigStore:
    def __init__(self, dsn: str): self._dsn = dsn
    async def _conn(self): return await asyncpg.connect(self._dsn)

    async def ensure_schema(self, conn) -> None:
        await conn.execute(f'''CREATE TABLE IF NOT EXISTS {APPLIED} (
            kind text NOT NULL, name text NOT NULL, data jsonb NOT NULL,
            updated_at timestamptz DEFAULT now(), PRIMARY KEY(kind, name))''')
        await conn.execute(f'''CREATE TABLE IF NOT EXISTS {STAGED} (
            kind text NOT NULL, name text NOT NULL, data jsonb NOT NULL, flag text NOT NULL,
            updated_at timestamptz DEFAULT now(), PRIMARY KEY(kind, name))''')

    @staticmethod
    def _rows(recs): return [{"kind": r["kind"], "name": r["name"], "data": json.loads(r["data"]),
                              **({"flag": r["flag"]} if "flag" in r else {})} for r in recs]

    async def applied(self) -> list[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return self._rows(await conn.fetch(f"SELECT kind,name,data FROM {APPLIED} ORDER BY kind,name"))
        finally: await conn.close()

    async def staged(self) -> list[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return self._rows(await conn.fetch(f"SELECT kind,name,data,flag FROM {STAGED} ORDER BY kind,name"))
        finally: await conn.close()

    async def stage(self, kind: str, name: str, data, *, deleted: bool = False) -> None:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            if deleted:
                flag = "deleted"
            else:
                has = await conn.fetchval(f"SELECT 1 FROM {APPLIED} WHERE kind=$1 AND name=$2", kind, name)
                flag = decide_flag(bool(has))
            await conn.execute(f'''INSERT INTO {STAGED}(kind,name,data,flag) VALUES($1,$2,$3,$4)
                ON CONFLICT(kind,name) DO UPDATE SET data=$3, flag=$4, updated_at=now()''',
                kind, name, json.dumps(data), flag)
        finally: await conn.close()

    async def clear_staged(self, kind: Optional[str] = None, name: Optional[str] = None) -> None:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            if kind and name: await conn.execute(f"DELETE FROM {STAGED} WHERE kind=$1 AND name=$2", kind, name)
            else: await conn.execute(f"DELETE FROM {STAGED}")
        finally: await conn.close()

    async def staged_count(self) -> int:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return int(await conn.fetchval(f"SELECT count(*) FROM {STAGED}"))
        finally: await conn.close()

    async def fold(self) -> None:
        """Apply staged into applied, then clear staged. Call only after a committed file write."""
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            async with conn.transaction():
                await conn.execute(f"DELETE FROM {APPLIED} a USING {STAGED} s WHERE a.kind=s.kind AND a.name=s.name AND s.flag='deleted'")
                await conn.execute(f'''INSERT INTO {APPLIED}(kind,name,data,updated_at)
                    SELECT kind,name,data,now() FROM {STAGED} WHERE flag IN ('new','changed')
                    ON CONFLICT(kind,name) DO UPDATE SET data=EXCLUDED.data, updated_at=now()''')
                await conn.execute(f"DELETE FROM {STAGED}")
        finally: await conn.close()

    async def seed_applied(self, items: list[dict]) -> None:
        """Bootstrap: populate applied from imported items (only if empty)."""
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            if await conn.fetchval(f"SELECT 1 FROM {APPLIED} LIMIT 1"): return
            async with conn.transaction():
                for it in items:
                    await conn.execute(f"INSERT INTO {APPLIED}(kind,name,data) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                                       it["kind"], it["name"], json.dumps(it["data"]))
        finally: await conn.close()
