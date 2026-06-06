from __future__ import annotations
from typing import Any, Optional
import asyncpg

RETENTION_TABLE = '"LiteLLM_SpendLogs"'
STATS_TABLES = ['"LiteLLM_SpendLogs"', '"LiteLLM_VerificationToken"', '"LiteLLM_ErrorLogs"']


def maintenance_sql(retention_days: int) -> dict[str, str]:
    # $1 = retention_days (interval built safely via make_interval); never string-interpolate input
    return {
        "trim_spend_logs": f'DELETE FROM {RETENTION_TABLE} WHERE "startTime" < (now() - make_interval(days => $1))',
        "delete_expired_keys": 'DELETE FROM "LiteLLM_VerificationToken" WHERE "expires" IS NOT NULL AND "expires" < now()',
    }


class DbAdmin:
    def __init__(self, dsn: str):
        self._dsn = dsn

    async def _conn(self):
        return await asyncpg.connect(self._dsn)

    async def stats(self) -> dict[str, Any]:
        conn = await self._conn()
        try:
            rows = {}
            for t in STATS_TABLES:
                try:
                    rows[t.strip('"')] = await conn.fetchval(f"SELECT count(*) FROM {t}")
                except asyncpg.UndefinedTableError:
                    rows[t.strip('"')] = None
            db_size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
            return {"row_counts": rows, "db_size": db_size}
        finally:
            await conn.close()

    async def run_maintenance(self, retention_days: int, delete_expired_keys: bool = True) -> dict[str, Any]:
        sql = maintenance_sql(retention_days)
        conn = await self._conn()
        try:
            trimmed = await conn.execute(sql["trim_spend_logs"], retention_days)  # returns "DELETE <n>"
            result = {"trimmed_spend_logs": _count(trimmed), "retention_days": retention_days}
            if delete_expired_keys:
                result["deleted_expired_keys"] = _count(await conn.execute(sql["delete_expired_keys"]))
            return result
        finally:
            await conn.close()


def _count(tag: str) -> int:
    # asyncpg execute() returns a command tag like "DELETE 12"
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0
