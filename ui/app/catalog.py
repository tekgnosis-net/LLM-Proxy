from __future__ import annotations
import json
import httpx
import asyncpg
from typing import Any, Optional

_SUPPORTS_PREFIX = "supports_"
_PRICING_FIELDS = ("input_cost_per_token", "output_cost_per_token", "max_input_tokens",
                   "max_output_tokens", "max_tokens", "mode", "litellm_provider")


def parse_pricing(data: dict) -> list[dict[str, Any]]:
    rows = []
    for name, v in (data or {}).items():
        if name == "sample_spec" or not isinstance(v, dict):
            continue
        row = {"model_name": name}
        for f in _PRICING_FIELDS:
            row[f] = v.get(f)
        row["supports"] = {k: val for k, val in v.items() if k.startswith(_SUPPORTS_PREFIX)}
        rows.append(row)
    return rows


def parse_endpoints(data: dict) -> list[dict[str, Any]]:
    providers = (data or {}).get("providers", {})
    rows = []
    for slug, v in providers.items():
        if not isinstance(v, dict):
            continue
        rows.append({"provider": slug, "display_name": v.get("display_name"),
                     "docs_url": v.get("url"), "endpoints": v.get("endpoints", {})})
    return rows


class Catalog:
    def __init__(self, dsn: str, pricing_url: str, endpoints_url: str,
                 transport: Optional[httpx.BaseTransport] = None):
        self._dsn = dsn; self._pricing_url = pricing_url; self._endpoints_url = endpoints_url
        self._transport = transport

    async def _conn(self): return await asyncpg.connect(self._dsn)

    async def ensure_schema(self, conn) -> None:
        await conn.execute('''CREATE TABLE IF NOT EXISTS ui_model_pricing (
            model_name text PRIMARY KEY, input_cost_per_token double precision,
            output_cost_per_token double precision, max_input_tokens bigint,
            max_output_tokens bigint, max_tokens bigint, mode text,
            litellm_provider text, supports jsonb, updated_at timestamptz default now())''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS ui_provider_endpoints (
            provider text PRIMARY KEY, display_name text, docs_url text,
            endpoints jsonb, updated_at timestamptz default now())''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS ui_catalog_meta (
            id int PRIMARY KEY DEFAULT 1, last_synced timestamptz, models int, providers int,
            last_error text)''')

    async def _fetch(self, url: str) -> dict:
        async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as c:
            r = await c.get(url); r.raise_for_status(); return r.json()

    async def sync(self) -> dict:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            try:
                pricing = parse_pricing(await self._fetch(self._pricing_url))
                endpoints = parse_endpoints(await self._fetch(self._endpoints_url))
            except Exception as e:
                await conn.execute("INSERT INTO ui_catalog_meta(id,last_error) VALUES(1,$1) "
                                   "ON CONFLICT(id) DO UPDATE SET last_error=$1", str(e))
                raise
            async with conn.transaction():
                for r in pricing:
                    await conn.execute(
                        '''INSERT INTO ui_model_pricing(model_name,input_cost_per_token,output_cost_per_token,
                           max_input_tokens,max_output_tokens,max_tokens,mode,litellm_provider,supports,updated_at)
                           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,now())
                           ON CONFLICT(model_name) DO UPDATE SET input_cost_per_token=$2,output_cost_per_token=$3,
                           max_input_tokens=$4,max_output_tokens=$5,max_tokens=$6,mode=$7,litellm_provider=$8,
                           supports=$9,updated_at=now()''',
                        r["model_name"], r["input_cost_per_token"], r["output_cost_per_token"],
                        r["max_input_tokens"], r["max_output_tokens"], r["max_tokens"], r["mode"],
                        r["litellm_provider"], json.dumps(r["supports"]))
                for r in endpoints:
                    await conn.execute(
                        '''INSERT INTO ui_provider_endpoints(provider,display_name,docs_url,endpoints,updated_at)
                           VALUES($1,$2,$3,$4,now()) ON CONFLICT(provider) DO UPDATE SET display_name=$2,
                           docs_url=$3,endpoints=$4,updated_at=now()''',
                        r["provider"], r["display_name"], r["docs_url"], json.dumps(r["endpoints"]))
            await conn.execute("INSERT INTO ui_catalog_meta(id,last_synced,models,providers,last_error) "
                               "VALUES(1,now(),$1,$2,NULL) ON CONFLICT(id) DO UPDATE SET "
                               "last_synced=now(),models=$1,providers=$2,last_error=NULL", len(pricing), len(endpoints))
            return {"models": len(pricing), "providers": len(endpoints)}
        finally:
            await conn.close()

    async def get_model(self, name: str) -> Optional[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)   # safe before first sync (relation may not exist yet)
            row = await conn.fetchrow("SELECT * FROM ui_model_pricing WHERE model_name=$1", name)
            if not row and "/" in name:                       # try the unprefixed name
                row = await conn.fetchrow("SELECT * FROM ui_model_pricing WHERE model_name=$1", name.split("/", 1)[1])
            return dict(row) if row else None
        finally:
            await conn.close()

    async def get_providers(self) -> list[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)   # safe before first sync (relation may not exist yet)
            rows = await conn.fetch("SELECT * FROM ui_provider_endpoints ORDER BY provider")
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def status(self) -> dict:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            row = await conn.fetchrow("SELECT last_synced,models,providers,last_error FROM ui_catalog_meta WHERE id=1")
            return dict(row) if row else {"last_synced": None, "models": 0, "providers": 0, "last_error": None}
        finally:
            await conn.close()
