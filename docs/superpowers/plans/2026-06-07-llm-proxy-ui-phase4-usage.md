# LLM-Proxy Admin UI — Phase 4 (Usage & Spend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD (httpx MockTransport). Frontend = build + real-stack verification. Steps use `- [ ]`.

**Goal:** A **Usage & Spend** dashboard — total spend, spend by model, spend by key (alias), and a daily activity time-series — read from LiteLLM's analytics endpoints (master key server-side).

**Architecture:** `spend_client` wraps the 4 verified endpoints; one resilient `GET /api/usage` fetches them concurrently (last-30-day window) and returns a combined object; `Usage.svelte` renders cards + simple CSS bars (no chart lib). With no traffic yet everything reads `$0`/empty — that's correct; the screen proves out once real calls flow.

**Tech Stack:** FastAPI, httpx, asyncio; Svelte 5. No new deps.

**API (verified vs litellm `main`, all need `disable_spend_logs:false`):**
- `GET /global/spend` → `{spend, max_budget}` (rolling 30d).
- `GET /global/spend/models?limit=10` → `[{model, total_spend}]`.
- `GET /global/spend/keys?limit=20` → `[{api_key(hashed), key_alias, key_name, total_spend}]`.
- `GET /global/activity?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` → `{daily_data:[{date:"Mon DD", api_requests, total_tokens}], sum_api_requests, sum_total_tokens}`.
(Views are rolling-30d; `/global/spend/report` is enterprise-only — avoid; `/spend/logs` is deprecated.)

---

## File Structure
```
ui/app/spend_client.py            # CREATE
ui/app/routes/usage_routes.py     # CREATE: GET /api/usage (combined)
ui/app/main.py                    # MODIFY: include usage_routes
ui/tests/test_spend_client.py     # CREATE
ui/tests/test_usage_routes.py     # CREATE
ui/frontend/src/lib/api.js        # MODIFY: usage()
ui/frontend/src/routes/Usage.svelte  # CREATE
ui/frontend/src/App.svelte        # MODIFY: nav + render
```

---

## Task 1: spend_client (TDD)

**Files:** Create `ui/app/spend_client.py`, `ui/tests/test_spend_client.py`.

- [ ] **Step 1: failing tests:**
```python
import httpx, pytest
from app.spend_client import SpendClient


def _c(handler):
    return SpendClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_total_spend():
    def h(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        return httpx.Response(200, json={"spend": 4.23, "max_budget": None})
    assert (await _c(h).total_spend())["spend"] == 4.23


@pytest.mark.asyncio
async def test_spend_by_model():
    def h(req):
        assert req.url.path.endswith("/global/spend/models")
        return httpx.Response(200, json=[{"model": "gpt-4o", "total_spend": 2.1}])
    rows = await _c(h).spend_by_model()
    assert rows[0]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_spend_by_key():
    def h(req):
        return httpx.Response(200, json=[{"api_key": "hash", "key_alias": "ci", "total_spend": 1.0}])
    rows = await _c(h).spend_by_key()
    assert rows[0]["key_alias"] == "ci"


@pytest.mark.asyncio
async def test_activity_passes_dates():
    seen = {}
    def h(req):
        seen["s"] = req.url.params.get("start_date"); seen["e"] = req.url.params.get("end_date")
        return httpx.Response(200, json={"daily_data": [{"date": "Jun 05", "api_requests": 10, "total_tokens": 100}], "sum_api_requests": 10, "sum_total_tokens": 100})
    out = await _c(h).activity("2026-05-08", "2026-06-07")
    assert seen == {"s": "2026-05-08", "e": "2026-06-07"}
    assert out["sum_api_requests"] == 10
```

- [ ] **Step 2: run red** → FAIL (module missing).

- [ ] **Step 3: implement `ui/app/spend_client.py`:**
```python
from __future__ import annotations
import httpx
from typing import Any, Optional


class SpendClient:
    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self):
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def total_spend(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/spend"); r.raise_for_status(); return r.json()

    async def spend_by_model(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/spend/models", params={"limit": limit})
            r.raise_for_status(); d = r.json(); return d if isinstance(d, list) else []

    async def spend_by_key(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/spend/keys", params={"limit": limit})
            r.raise_for_status(); d = r.json(); return d if isinstance(d, list) else []

    async def activity(self, start_date: str, end_date: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/global/activity", params={"start_date": start_date, "end_date": end_date})
            r.raise_for_status(); return r.json()
```

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): spend_client (total/by-model/by-key/activity)`.

---

## Task 2: usage route (TDD)

**Files:** Create `ui/app/routes/usage_routes.py`, `ui/tests/test_usage_routes.py`; Modify `ui/app/main.py`.

- [ ] **Step 1: failing tests** (fake client via `make_spend_client` seam):
```python
import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw"); os.environ["SESSION_SECRET"] = "s"
    os.environ["CONFIG_PATH"] = str(tmp_path / "c.yaml"); (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.usage_routes as ur
    ur.make_spend_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


class FakeSpend:
    async def total_spend(self): return {"spend": 4.23, "max_budget": None}
    async def spend_by_model(self): return [{"model": "gpt-4o", "total_spend": 2.1}]
    async def spend_by_key(self): return [{"key_alias": "ci", "total_spend": 1.0}]
    async def activity(self, s, e): return {"daily_data": [], "sum_api_requests": 0, "sum_total_tokens": 0}


def test_usage_requires_login(tmp_path):
    c = _client(tmp_path, FakeSpend()); c.cookies.clear()
    assert c.get("/api/usage").status_code == 401


def test_usage_combines(tmp_path):
    c = _client(tmp_path, FakeSpend())
    d = c.get("/api/usage").json()
    assert d["total"]["spend"] == 4.23
    assert d["by_model"][0]["model"] == "gpt-4o"
    assert d["by_key"][0]["key_alias"] == "ci"
    assert "activity" in d


def test_usage_resilient_to_partial_failure(tmp_path):
    class Partial(FakeSpend):
        async def spend_by_model(self): raise RuntimeError("boom")
    d = _client(tmp_path, Partial()).get("/api/usage").json()
    assert d["total"]["spend"] == 4.23     # other sections still present
    assert d["by_model"] == []             # failed section degrades to empty
```

- [ ] **Step 2: run red** → FAIL.

- [ ] **Step 3: implement `ui/app/routes/usage_routes.py`:**
```python
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from app.auth import login_required
from app.spend_client import SpendClient
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_spend_client() -> SpendClient:
    s = get_settings()
    return SpendClient(s.litellm_base_url, s.litellm_master_key)


async def _safe(coro, default):
    try:
        return await coro
    except Exception:
        return default


@router.get("/usage", dependencies=[Depends(login_required)])
async def usage():
    client = make_spend_client()
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=30)
    total, by_model, by_key, activity = await asyncio.gather(
        _safe(client.total_spend(), {"spend": 0, "max_budget": None}),
        _safe(client.spend_by_model(), []),
        _safe(client.spend_by_key(), []),
        _safe(client.activity(start.isoformat(), end.isoformat()),
              {"daily_data": [], "sum_api_requests": 0, "sum_total_tokens": 0}),
    )
    return {"total": total, "by_model": by_model, "by_key": by_key, "activity": activity,
            "window": {"start": start.isoformat(), "end": end.isoformat()}}
```

- [ ] **Step 4: wire into `main.py`** (`from app.routes import ... usage_routes` + `app.include_router(usage_routes.router)`).
- [ ] **Step 5: green + full suite. Step 6: commit** `feat(ui): /api/usage (combined spend/activity, resilient)`.

---

## Task 3: Usage screen + nav

**Files:** Modify `ui/frontend/src/lib/api.js`, `ui/frontend/src/App.svelte`; Create `ui/frontend/src/routes/Usage.svelte`.

- [ ] **Step 1: api.js** — add `usage: () => req('/api/usage')`.

- [ ] **Step 2: create `ui/frontend/src/routes/Usage.svelte`:**
```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let d = $state(null); let err = $state(''); let loading = $state(true)
  onMount(async () => { try { d = await api.usage() } catch (e) { err = e.message } finally { loading = false } })
  const money = (n) => `$${Number(n ?? 0).toFixed(2)}`
  function maxModel() { return Math.max(1, ...((d?.by_model ?? []).map(m => m.total_spend ?? 0))) }
  function maxReq() { return Math.max(1, ...((d?.activity?.daily_data ?? []).map(x => x.api_requests ?? 0))) }
</script>

<div class="page">
  <h1>Usage &amp; Spend <span class="sub">last 30 days</span></h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if loading}<p class="empty">Loading…</p>
  {:else if d}
    <div class="cards">
      <div class="card stat"><div class="label">Total spend</div><div class="big">{money(d.total?.spend)}</div></div>
      <div class="card stat"><div class="label">Requests (30d)</div><div class="big">{d.activity?.sum_api_requests ?? 0}</div></div>
      <div class="card stat"><div class="label">Tokens (30d)</div><div class="big">{(d.activity?.sum_total_tokens ?? 0).toLocaleString()}</div></div>
    </div>

    <div class="card">
      <h2>Spend by model</h2>
      {#if (d.by_model ?? []).length === 0}<p class="empty">No spend recorded yet.</p>
      {:else}{#each d.by_model as m}
        <div class="bar-row"><span class="bk">{m.model}</span>
          <div class="bar"><div class="fill" style="width:{Math.round((m.total_spend ?? 0)/maxModel()*100)}%"></div></div>
          <span class="bv">{money(m.total_spend)}</span></div>
      {/each}{/if}
    </div>

    <div class="card">
      <h2>Spend by key</h2>
      {#if (d.by_key ?? []).length === 0}<p class="empty">No key spend yet.</p>
      {:else}<table><thead><tr><th>Key</th><th>Spend</th></tr></thead><tbody>
        {#each d.by_key as k}<tr><td>{k.key_alias || k.key_name || '—'}</td><td>{money(k.total_spend)}</td></tr>{/each}
      </tbody></table>{/if}
    </div>

    <div class="card">
      <h2>Daily requests</h2>
      {#if (d.activity?.daily_data ?? []).length === 0}<p class="empty">No activity in this window.</p>
      {:else}<div class="spark">{#each d.activity.daily_data as x}
        <div class="col" title="{x.date}: {x.api_requests} req"><div class="colfill" style="height:{Math.round((x.api_requests ?? 0)/maxReq()*100)}%"></div></div>
      {/each}</div>{/if}
    </div>
  {/if}
</div>

<style>
  .page{padding:24px 30px;max-width:960px}.sub{font-size:13px;color:#6e6e73;font-weight:400}
  .cards{display:flex;gap:14px;margin:14px 0}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.stat{flex:1;margin-top:0}.label{font-size:12px;color:#6e6e73}.big{font-size:28px;font-weight:600;margin-top:6px}
  h2{font-size:15px;margin:0 0 10px}
  .bar-row{display:grid;grid-template-columns:160px 1fr 70px;align-items:center;gap:10px;margin:6px 0;font-size:13px}
  .bar{background:#f0f0f2;border-radius:6px;height:14px;overflow:hidden}.fill{height:100%;background:#0a84ff}
  .bk{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bv{text-align:right;color:#3a3a3c}
  table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .spark{display:flex;align-items:flex-end;gap:3px;height:80px}
  .col{flex:1;background:#f0f0f2;border-radius:3px 3px 0 0;display:flex;align-items:flex-end;min-width:4px}
  .colfill{width:100%;background:#34c759;border-radius:3px 3px 0 0;min-height:2px}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}.empty{color:#6e6e73}
</style>
```

- [ ] **Step 3: wire into `App.svelte`** — `import Usage from './routes/Usage.svelte'`; add to the Overview nav group: `<button class="nav" class:active={screen==='usage'} onclick={() => screen='usage'}>📊 Usage &amp; Spend</button>`; render branch `{:else if screen==='usage'}<Usage />`.

- [ ] **Step 4: build** + commit `feat(ui): Usage & Spend screen (spend cards, by-model/key, activity)`.

---

## Task 4: Real-stack integration verification

- [ ] **Step 1:** build + up; log in; open **Usage & Spend**. With no traffic it shows `$0.00`, empty model/key sections, "No activity" — confirm it renders WITHOUT errors (the resilient route returns zeros, not 500s).
- [ ] **Step 2:** `curl -s -b cookie http://localhost:8081/api/usage` → JSON with `total/by_model/by_key/activity/window` keys, no error. Confirm the 4 upstream calls succeeded (or degraded to empty) — check `docker logs litellm-ui` for any spend_client errors.
- [ ] **Step 3:** Tear down.

## Self-Review
- **Spec coverage:** total spend ✓, by model ✓, by key ✓, daily activity ✓; master key server-side ✓; resilient to a partial upstream failure (gather + _safe) ✓; correct 30d window + YYYY-MM-DD dates ✓.
- **Realism:** with no usage, all zeros/empty — documented; screen still renders.
- **Type consistency:** `SpendClient.{total_spend,spend_by_model,spend_by_key,activity}`, `make_spend_client` seam, `api.usage` consistent.

## Follow-on
Phase 5 (caching config + DB housekeeping + export/import + dark mode), then docs + screenshots + LiteLLM credit.
