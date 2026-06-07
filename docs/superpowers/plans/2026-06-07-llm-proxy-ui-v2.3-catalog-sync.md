# LLM-Proxy Admin UI — v2.3 (LiteLLM catalog syncs) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD (pure parse + routes; DB upsert verified in integration). Frontend = build + real-stack verification. Steps use `- [ ]`. **Builds on v2.1 + v2.2.**

**Goal:** Periodically sync LiteLLM's **pricing/context catalog** (`model_prices_and_context_window.json`) and **provider-endpoints matrix** (`provider_endpoints_support.json`) into Postgres, and use them to **auto-fill** the Models form (cost/context/mode) and **narrow endpoint options** per provider.

**Architecture:** A `catalog` module (asyncpg) creates two tables, fetches+parses+upserts the two GitHub JSONs (pure parse functions, TDD-able), and serves lookups. An APScheduler job (reusing the housekeeping lifespan scheduler) refreshes on a configurable cadence (default 7 days) + a manual "Sync now". Fetch failures keep last-good. `api_base` is NOT sourced (no URLs in the JSON).

**Tech Stack:** FastAPI, asyncpg, apscheduler, httpx (all already deps from housekeeping). Svelte 5.

**Spec:** [`../specs/2026-06-07-llm-proxy-ui-v2-design.md`](../specs/2026-06-07-llm-proxy-ui-v2-design.md) (§ Phase v2.3). **Verified URLs:** `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json` (~1.5 MB, ~2775 models, skip `sample_spec`); `https://raw.githubusercontent.com/BerriAI/litellm/main/provider_endpoints_support.json` (~90 KB, 157 providers).

---

## File Structure
```
ui/app/catalog.py                 # CREATE: parse fns + Catalog (asyncpg: schema/sync/lookups)
ui/app/routes/catalog_routes.py   # CREATE: /api/catalog/{model,providers,status,sync}
ui/app/settings.py                # MODIFY: catalog_sync_* + URLs
ui/app/main.py                    # MODIFY: include catalog_routes; add catalog cron to lifespan
ui/tests/test_catalog.py          # CREATE: parse fns (pure)
ui/tests/test_catalog_routes.py   # CREATE
ui/frontend/src/lib/api.js        # MODIFY: catalog helpers
ui/frontend/src/routes/Models.svelte    # MODIFY: auto-fill on model/provider change
ui/frontend/src/routes/Settings.svelte  # MODIFY: Catalog panel (last-synced + Sync now)
```

---

## Task 1: settings + catalog parse functions (TDD)

**Files:** Modify `ui/app/settings.py`; Create `ui/app/catalog.py`, `ui/tests/test_catalog.py`.

- [ ] **Step 1: settings.py** add:
```python
    catalog_sync_enabled: bool = True
    catalog_sync_interval_days: int = 7
    catalog_pricing_url: str = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    catalog_endpoints_url: str = "https://raw.githubusercontent.com/BerriAI/litellm/main/provider_endpoints_support.json"
```

- [ ] **Step 2: failing tests** (`ui/tests/test_catalog.py`) — the pure parsers:
```python
from app.catalog import parse_pricing, parse_endpoints


def test_parse_pricing_skips_sample_spec_and_extracts_fields():
    data = {
        "sample_spec": {"input_cost_per_token": 0, "note": "doc only"},
        "gpt-4o": {"litellm_provider": "openai", "mode": "chat",
                   "input_cost_per_token": 2.5e-06, "output_cost_per_token": 1e-05,
                   "max_input_tokens": 128000, "max_output_tokens": 16384,
                   "supports_vision": True, "supports_function_calling": True},
    }
    rows = parse_pricing(data)
    assert "sample_spec" not in [r["model_name"] for r in rows]
    row = next(r for r in rows if r["model_name"] == "gpt-4o")
    assert row["input_cost_per_token"] == 2.5e-06 and row["mode"] == "chat" and row["litellm_provider"] == "openai"
    assert row["max_input_tokens"] == 128000
    assert row["supports"]["supports_vision"] is True


def test_parse_pricing_tolerates_sparse_entries():
    rows = parse_pricing({"text-embedding-3-small": {"litellm_provider": "openai", "mode": "embedding",
                                                     "input_cost_per_token": 2e-08}})
    r = rows[0]
    assert r["output_cost_per_token"] is None and r["max_input_tokens"] is None and r["mode"] == "embedding"


def test_parse_endpoints_extracts_provider_matrix():
    data = {"_comment": "x", "_schema": {}, "endpoints": {},
            "providers": {"anthropic": {"display_name": "Anthropic", "url": "https://docs…",
                                        "endpoints": {"chat_completions": True, "embeddings": False}}}}
    rows = parse_endpoints(data)
    r = next(x for x in rows if x["provider"] == "anthropic")
    assert r["display_name"] == "Anthropic" and r["endpoints"]["chat_completions"] is True
    assert all(x["provider"] not in ("_comment", "_schema", "endpoints") for x in rows)
```

- [ ] **Step 3: run red** → FAIL. **Step 4: implement** the parsers + the `Catalog` class in `ui/app/catalog.py`:
```python
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
            row = await conn.fetchrow("SELECT * FROM ui_model_pricing WHERE model_name=$1", name)
            if not row and "/" in name:                       # try the unprefixed name
                row = await conn.fetchrow("SELECT * FROM ui_model_pricing WHERE model_name=$1", name.split("/", 1)[1])
            return dict(row) if row else None
        finally:
            await conn.close()

    async def get_providers(self) -> list[dict]:
        conn = await self._conn()
        try:
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
```

- [ ] **Step 5: green + full suite.** **Step 6: commit** `feat(ui): catalog parse fns + Catalog (pricing/endpoints sync)`.

---

## Task 2: catalog routes + scheduler (TDD)

**Files:** Create `ui/app/routes/catalog_routes.py`, `ui/tests/test_catalog_routes.py`; Modify `ui/app/main.py`.

- [ ] **Step 1: failing tests** (fake catalog via `make_catalog` seam):
```python
import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password

def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path/"c.yaml"), DATABASE_URL="postgresql://x")
    (tmp_path/"c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.catalog_routes as cat
    cat.make_catalog = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password":"pw"}); return c

class FakeCatalog:
    async def get_model(self,n): return {"model_name":n,"input_cost_per_token":2.5e-6,"output_cost_per_token":1e-5,"mode":"chat","max_input_tokens":128000} if n=="gpt-4o" else None
    async def get_providers(self): return [{"provider":"openai","display_name":"OpenAI","endpoints":{"chat_completions":True}}]
    async def status(self): return {"last_synced":"2026-06-07T00:00:00Z","models":2775,"providers":157,"last_error":None}
    async def sync(self): return {"models":2775,"providers":157}

def test_requires_login(tmp_path):
    c=_client(tmp_path,FakeCatalog()); c.cookies.clear(); assert c.get("/api/catalog/status").status_code==401
def test_get_model(tmp_path):
    r=_client(tmp_path,FakeCatalog()).get("/api/catalog/model/gpt-4o"); assert r.json()["mode"]=="chat"
def test_get_model_404(tmp_path):
    assert _client(tmp_path,FakeCatalog()).get("/api/catalog/model/unknown").status_code==404
def test_providers(tmp_path):
    assert _client(tmp_path,FakeCatalog()).get("/api/catalog/providers").json()[0]["provider"]=="openai"
def test_status_and_sync(tmp_path):
    c=_client(tmp_path,FakeCatalog())
    assert c.get("/api/catalog/status").json()["models"]==2775
    assert c.post("/api/catalog/sync").json()["models"]==2775
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement `ui/app/routes/catalog_routes.py`:**
```python
from fastapi import APIRouter, Depends, HTTPException
from app.auth import login_required
from app.catalog import Catalog
from app.settings import get_settings

router = APIRouter(prefix="/api")

def make_catalog() -> Catalog:
    s = get_settings()
    if not s.database_url: raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return Catalog(s.database_url, s.catalog_pricing_url, s.catalog_endpoints_url)

@router.get("/catalog/model/{name:path}", dependencies=[Depends(login_required)])
async def catalog_model(name: str):
    try: m = await make_catalog().get_model(name)
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"catalog error: {e}")
    if not m: raise HTTPException(status_code=404, detail="model not in catalog")
    return m

@router.get("/catalog/providers", dependencies=[Depends(login_required)])
async def catalog_providers():
    try: return await make_catalog().get_providers()
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"catalog error: {e}")

@router.get("/catalog/status", dependencies=[Depends(login_required)])
async def catalog_status():
    try: return await make_catalog().status()
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"catalog error: {e}")

@router.post("/catalog/sync", dependencies=[Depends(login_required)])
async def catalog_sync():
    try: return await make_catalog().sync()
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"catalog sync failed: {e}")
```

- [ ] **Step 4: scheduler** — in `main.py` lifespan, alongside the housekeeping job, add (when `catalog_sync_enabled and database_url`):
```python
    if s.catalog_sync_enabled and s.database_url:
        from app.catalog import Catalog
        sched = sched or AsyncIOScheduler()
        async def catalog_job():
            try: await Catalog(s.database_url, s.catalog_pricing_url, s.catalog_endpoints_url).sync()
            except Exception: pass
        sched.add_job(catalog_job, "interval", days=s.catalog_sync_interval_days, id="catalog",
                      next_run_time=datetime.now(timezone.utc))   # also run once shortly after boot
```
(Reuse the existing `sched`/`datetime` already imported for housekeeping; ensure `sched.start()` runs if any job was added; shutdown in finally.) Include `catalog_routes.router`.

- [ ] **Step 5: green + full suite.** **Step 6: commit** `feat(ui): /api/catalog routes + scheduled sync (default weekly + boot)`.

---

## Task 3: frontend — Models auto-fill + Settings catalog panel

**Files:** Modify `ui/frontend/src/lib/api.js`, `ui/frontend/src/routes/Models.svelte`, `ui/frontend/src/routes/Settings.svelte`.

- [ ] **Step 1: api.js:**
```javascript
  catalogModel: (name) => req(`/api/catalog/model/${encodeURIComponent(name)}`),
  catalogProviders: () => req('/api/catalog/providers'),
  catalogStatus: () => req('/api/catalog/status'),
  catalogSync: () => req('/api/catalog/sync', { method: 'POST' }),
```

- [ ] **Step 2: Models.svelte auto-fill** — when the constructed model string changes (provider prefix + modelId) and the user hasn't manually overridden cost/mode, look it up:
```javascript
  let autofilled = $state(false)
  async function tryAutofill() {
    const full = provider.prefix + form.modelId
    if (!form.modelId) return
    try {
      const m = await api.catalogModel(full)
      if (m) {
        if (!form.input_cost) form.input_cost = m.input_cost_per_token ?? ''
        if (!form.output_cost) form.output_cost = m.output_cost_per_token ?? ''
        if (m.mode) form.mode = m.mode
        autofilled = true
      }
    } catch { /* 404 = not in catalog; leave fields */ }
  }
  // call tryAutofill() on modelId blur (on:blur) or a "Look up" button next to Provider model id
```
Add a small **"Look up pricing"** button (or on-blur) next to the model-id field; show `{#if autofilled}<span class="ok">auto-filled from catalog</span>{/if}`. Costs remain editable (override).
Optionally narrow `mode` options using `api.catalogProviders()` for the selected provider's `endpoints` (map endpoint→mode); fallback to the static list. Keep this best-effort.

- [ ] **Step 3: Settings.svelte — Catalog panel:**
```svelte
  <div class="card"><h2>LiteLLM catalog</h2>
    <p class="hint">Model prices/context + provider endpoints, synced from the LiteLLM repo and used to auto-fill Models.</p>
    {#if catStatus}<p>Last synced: <strong>{catStatus.last_synced ? new Date(catStatus.last_synced).toLocaleString() : 'never'}</strong>
      · {catStatus.models} models · {catStatus.providers} providers{catStatus.last_error ? ` · last error: ${catStatus.last_error}` : ''}</p>{/if}
    <button onclick={syncCatalog} disabled={catBusy}>{catBusy ? 'Syncing…' : 'Sync now'}</button>
    {#if catMsg}<div class="banner ok">{catMsg}</div>{/if}
  </div>
```
Script: `let catStatus=$state(null),catBusy=$state(false),catMsg=$state('')`; `onMount`→`api.catalogStatus().then(s=>catStatus=s).catch(()=>{})`; `syncCatalog`→`catBusy=true; try{const r=await api.catalogSync(); catMsg=`Synced ${r.models} models, ${r.providers} providers`; catStatus=await api.catalogStatus()}catch(e){catMsg=e.message}finally{catBusy=false}`.

- [ ] **Step 4: build** + commit `feat(ui): Models catalog auto-fill + Settings catalog panel`.

---

## Task 4: real-stack integration verification

- [ ] **Step 1:** build + up; log in. **Settings → LiteLLM catalog → Sync now** → after a moment, "Synced ~2775 models, 157 providers"; `GET /api/catalog/status` shows counts + last_synced.
- [ ] **Step 2:** `GET /api/catalog/model/gpt-4o` returns input/output cost + mode + context. In **Models**, enter model id `gpt-4o` (OpenAI) → **Look up pricing** auto-fills cost + mode; values remain editable.
- [ ] **Step 3:** Confirm the scheduled job ran near boot (`ui_catalog_meta.last_synced` populated) and that a fetch failure (simulate by pointing the URL at a bad host via env) sets `last_error` but keeps prior rows. Tear down.

## Self-Review
- **Spec coverage:** pricing + endpoints sync to Postgres (T1) ✓; configurable cadence + boot + manual sync (T2) ✓; Models auto-fill cost/mode + provider endpoints (T3) ✓; last-good on failure + status (T1,T2) ✓; no `api_base` from catalog (by design) ✓.
- **Placeholders:** Task 3's auto-fill/sync handlers give exact api calls + the field wiring; the implementer extends the known Models/Settings files. Parse fns + routes + Catalog have full code.
- **Type consistency:** `parse_pricing`/`parse_endpoints`/`Catalog.{ensure_schema,sync,get_model,get_providers,status}`, `make_catalog` seam, `api.{catalogModel,catalogProviders,catalogStatus,catalogSync}` consistent. DB tables `ui_model_pricing`/`ui_provider_endpoints`/`ui_catalog_meta`.

## Notes
- DB upserts are integration-verified (not unit-tested) — unit tests cover the pure parsers + the routes (fake catalog). This matches the housekeeping pattern.
- `ui_*` table prefix avoids colliding with LiteLLM's own tables.
