# Richer Usage & Spend Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Backend = TDD (`cd ui && .venv/bin/python -m pytest -q`). Frontend (Svelte 5) = build (`cd ui/frontend && npm run build`) + Playwright on a **LAN-IP origin** (`http://10.0.20.85:8081`, NOT localhost). Steps use `- [ ]`. **Branch: `v3.9-usage-dashboard`** (already created).

**Goal:** Turn the basic Usage & Spend screen into a balanced operational+cost dashboard — KPIs (incl. latency/error/cache), by-provider/model/key breakdowns with latency & cost/1M, interactive time-series charts, and a recent-activity feed.

**Architecture:** Extend `GET /api/usage/summary` to return the full dashboard payload (SQL on `LiteLLM_SpendLogs`); add `GET /api/usage/recent` for the feed. Frontend reworks `Usage.svelte` into a dashboard with a uPlot chart wrapper, preserving the v3.8 range + saved auto-refresh.

**Tech Stack:** FastAPI, asyncpg, Postgres 16 (`LiteLLM_SpendLogs`), Svelte 5 runes, uPlot (~40KB).

## Global Constraints
- Releases as **`1.19.0`** from branch `v3.9-usage-dashboard`.
- Backend query failures: **log loudly + return `{... , "error": "query_failed"}`** — never silent zeros (v3.7.1 rule).
- Preserve v3.8 behavior: range selector, **saved** auto-refresh interval, `localStorage["usage.days"|"usage.refreshSec"]`, pause-when-hidden.
- Latency values are integer **milliseconds** (`request_duration_ms`); money is float; tokens are ints.
- Verify all SQL against host data (`ssh kumar@10.0.20.75`, `docker compose exec -T postgres psql`) — esp. the **cache_hit** metric (a naive formula gave a bogus 100%).

---

## Task 1: Backend — extend `/api/usage/summary`

**Files:** Modify `ui/app/routes/usage_routes.py`. Modify `ui/tests/test_usage_summary.py`.

**Interfaces:**
- Produces: `_shape_summary(days:int, granularity:str, kpis:dict, by_provider:list[dict], by_model:list[dict], by_key:list[dict], timeseries:list[dict]) -> dict` and the reworked `GET /api/usage/summary?days=N` returning the spec's payload (`range_days`, `granularity`, `kpis`, `by_provider`, `by_model`, `by_key`, `timeseries`, `error`).

- [ ] **Step 1: Replace the old shaper tests.** In `ui/tests/test_usage_summary.py`, REPLACE the existing `test_shape_summary_*` tests (the old 5-arg shape is gone) with:
```python
import types, pytest
from datetime import datetime, date
from app.routes.usage_routes import _shape_summary

def _row(label, **kw):
    base = {"label": label, "requests": 10, "tok_in": 100, "tok_out": 20, "spend": 1.5,
            "cost_per_1m": 0.05, "p50_ms": 200, "p95_ms": 900, "err_pct": 2.5}
    base.update(kw); return base

def test_shape_summary_maps_everything():
    kpis = {"spend": 1.5, "requests": 10, "tok_in": 100, "tok_out": 20, "error_rate": 0.1,
            "avg_latency_ms": 880.6, "p95_latency_ms": 900.4, "cache_hit_rate": 0.25}
    out = _shape_summary(30, "day", kpis, [_row("deepinfra")], [_row("gpt-oss-20b")],
                         [_row("team-a", last_used=datetime(2026,6,19,9,0))],
                         [{"bucket": date(2026,6,19), "requests": 5, "spend": 0.3, "p95_ms": 700.7}])
    assert out["range_days"] == 30 and out["granularity"] == "day"
    assert out["kpis"]["avg_latency_ms"] == 881 and out["kpis"]["cache_hit_rate"] == 0.25
    assert out["by_provider"][0]["label"] == "deepinfra" and out["by_provider"][0]["p95_ms"] == 900
    assert out["by_key"][0]["last_used"] == "2026-06-19T09:00:00"
    assert "last_used" not in out["by_provider"][0]
    assert out["timeseries"][0] == {"bucket": "2026-06-19", "requests": 5, "spend": 0.3, "p95_ms": 701}

def test_shape_summary_none_guards():
    kpis = {"spend": None, "requests": 0, "tok_in": None, "tok_out": None, "error_rate": None,
            "avg_latency_ms": None, "p95_latency_ms": None, "cache_hit_rate": None}
    out = _shape_summary(7, "hour", kpis, [], [], [], [])
    assert out["kpis"] == {"spend": 0.0, "requests": 0, "tok_in": 0, "tok_out": 0, "error_rate": 0.0,
                           "avg_latency_ms": None, "p95_latency_ms": None, "cache_hit_rate": None}
    assert out["by_provider"] == [] and out["timeseries"] == []

def test_shape_summary_row_cost_none():
    out = _shape_summary(7, "day", {}, [_row("x", cost_per_1m=None)], [], [], [])
    assert out["by_provider"][0]["cost_per_1m"] is None
```
- [ ] **Step 2: Run → FAIL** (`_shape_summary` arity changed). `cd ui && .venv/bin/python -m pytest tests/test_usage_summary.py -v`.
- [ ] **Step 3: Replace `_shape_summary`** in `usage_routes.py` with:
```python
def _ms(v):
    return int(round(v)) if v is not None else None

def _cols(rows):
    out = []
    for r in rows:
        d = {"label": r["label"], "requests": r["requests"], "tok_in": r["tok_in"] or 0,
             "tok_out": r["tok_out"] or 0, "spend": float(r["spend"] or 0),
             "cost_per_1m": float(r["cost_per_1m"]) if r.get("cost_per_1m") is not None else None,
             "p50_ms": _ms(r.get("p50_ms")), "p95_ms": _ms(r.get("p95_ms")),
             "err_pct": float(r["err_pct"] or 0)}
        if r.get("last_used"):
            d["last_used"] = r["last_used"].isoformat()
        out.append(d)
    return out

def _shape_summary(days, granularity, kpis, by_provider, by_model, by_key, timeseries):
    k = kpis or {}
    return {
        "range_days": days, "granularity": granularity,
        "kpis": {"spend": float(k.get("spend") or 0), "requests": k.get("requests") or 0,
                 "tok_in": k.get("tok_in") or 0, "tok_out": k.get("tok_out") or 0,
                 "error_rate": float(k.get("error_rate") or 0),
                 "avg_latency_ms": _ms(k.get("avg_latency_ms")), "p95_latency_ms": _ms(k.get("p95_latency_ms")),
                 "cache_hit_rate": float(k["cache_hit_rate"]) if k.get("cache_hit_rate") is not None else None},
        "by_provider": _cols(by_provider), "by_model": _cols(by_model), "by_key": _cols(by_key),
        "timeseries": [{"bucket": r["bucket"].isoformat(), "requests": r["requests"],
                        "spend": float(r["spend"] or 0), "p95_ms": _ms(r.get("p95_ms"))} for r in timeseries],
    }
```
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Replace the `/api/usage/summary` endpoint** body with the dashboard queries. A shared metrics column-list (DRY) keeps the three breakdowns identical:
```python
# NOTE: every query aliases the table as `l` so these shared metric columns
# work verbatim in the by_key JOIN too (LiteLLM_VerificationToken also has a
# `spend` column → bare `spend` would be ambiguous; `l.`-prefix avoids it).
_METRIC_COLS = (
    "COUNT(*) requests, SUM(l.prompt_tokens) tok_in, SUM(l.completion_tokens) tok_out, "
    "SUM(l.spend) spend, SUM(l.spend)/NULLIF(SUM(l.total_tokens),0)*1e6 cost_per_1m, "
    "percentile_cont(0.5) WITHIN GROUP (ORDER BY l.request_duration_ms) "
    "  FILTER (WHERE l.request_duration_ms>0) p50_ms, "
    "percentile_cont(0.95) WITHIN GROUP (ORDER BY l.request_duration_ms) "
    "  FILTER (WHERE l.request_duration_ms>0) p95_ms, "
    "100.0*COUNT(*) FILTER (WHERE l.status='failure')/NULLIF(COUNT(*),0) err_pct"
)

@router.get("/usage/summary", dependencies=[Depends(login_required)])
async def usage_summary(days: int = 30):
    days = max(1, min(int(days), 365))
    gran = "hour" if days <= 2 else "day"
    dsn = get_settings().database_url
    if not dsn:
        return _shape_summary(days, gran, {}, [], [], [], [])
    conn = await asyncpg.connect(dsn)
    W = 'WHERE l."startTime" > now() - make_interval(days => $1)'
    try:
        kpis = await conn.fetchrow(
            'SELECT COALESCE(SUM(l.spend),0) spend, COUNT(*) requests, '
            'COALESCE(SUM(l.prompt_tokens),0) tok_in, COALESCE(SUM(l.completion_tokens),0) tok_out, '
            "COUNT(*) FILTER (WHERE l.status='failure')::float/NULLIF(COUNT(*),0) error_rate, "
            'AVG(l.request_duration_ms) FILTER (WHERE l.request_duration_ms>0) avg_latency_ms, '
            'percentile_cont(0.95) WITHIN GROUP (ORDER BY l.request_duration_ms) '
            '  FILTER (WHERE l.request_duration_ms>0) p95_latency_ms, '
            "COUNT(*) FILTER (WHERE l.cache_hit IN ('True','true'))::float/NULLIF(COUNT(*),0) cache_hit_rate "
            f'FROM "LiteLLM_SpendLogs" l {W}', days)
        by_provider = await conn.fetch(
            f"SELECT COALESCE(NULLIF(l.custom_llm_provider,''),'(none)') label, {_METRIC_COLS} "
            f'FROM "LiteLLM_SpendLogs" l {W} GROUP BY label ORDER BY requests DESC NULLS LAST LIMIT 50', days)
        by_model = await conn.fetch(
            f"SELECT COALESCE(NULLIF(l.model,''),'(none)') label, {_METRIC_COLS} "
            f'FROM "LiteLLM_SpendLogs" l {W} GROUP BY label ORDER BY requests DESC NULLS LAST LIMIT 50', days)
        by_key = await conn.fetch(
            f"SELECT COALESCE(v.key_alias, LEFT(l.api_key,10)) label, MAX(l.\"startTime\") last_used, {_METRIC_COLS} "
            'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token=l.api_key '
            f'{W} GROUP BY label ORDER BY requests DESC NULLS LAST LIMIT 50', days)
        timeseries = await conn.fetch(
            f"SELECT date_trunc('{gran}', l.\"startTime\") bucket, COUNT(*) requests, "
            'COALESCE(SUM(l.spend),0) spend, '
            'percentile_cont(0.95) WITHIN GROUP (ORDER BY l.request_duration_ms) '
            '  FILTER (WHERE l.request_duration_ms>0) p95_ms '
            f'FROM "LiteLLM_SpendLogs" l {W} GROUP BY bucket ORDER BY bucket', days)
    except Exception:
        log.exception("usage_summary query failed (days=%s)", days)
        out = _shape_summary(days, gran, {}, [], [], [], [])
        out["error"] = "query_failed"
        return out
    finally:
        await conn.close()
    return _shape_summary(days, gran, dict(kpis), [dict(r) for r in by_provider],
                          [dict(r) for r in by_model], [dict(r) for r in by_key],
                          [dict(r) for r in timeseries])
```
- [ ] **Step 6: Run full suite green** (`pytest -q`). **Then verify the SQL on host data** (the by_key string-munging + FILTER-on-percentile are the risky bits): `ssh kumar@10.0.20.75 'cd ~/docker-apps/LLM-Proxy && U=$(grep -E "^POSTGRES_USER=" .env|cut -d= -f2); docker compose exec -T postgres psql -U "$U" -d litellm -c "..."'` — run the kpis, by_key, and timeseries queries (substitute `make_interval(days => 30)`); confirm they return sane rows with no error. **Resolve cache_hit_rate here:** if `hits/total` is misleading (most rows `None`), either keep it (UI labels it "of logged") or set it null — document the decision in a code comment.
- [ ] **Step 7: Commit** `git add ui/app/routes/usage_routes.py ui/tests/test_usage_summary.py && git commit -m "feat(ui): /api/usage/summary returns full dashboard (KPIs, by-provider/model/key, timeseries)"`

---

## Task 2: Backend — `/api/usage/recent` (activity feed)

**Files:** Modify `ui/app/routes/usage_routes.py`, `ui/tests/test_usage_summary.py`.

**Interfaces:**
- Produces: `_shape_recent(rows:list[dict]) -> dict` (`{"recent":[...]}`); `GET /api/usage/recent?limit=N`.

- [ ] **Step 1: Failing test** (append to `test_usage_summary.py`):
```python
from app.routes.usage_routes import _shape_recent
from datetime import datetime as _dt

def test_shape_recent_maps_rows():
    rows = [{"time": _dt(2026,6,19,18,42,3), "model": "deepinfra/openai/gpt-oss-20b",
             "provider": "deepinfra", "key": "hindsight-cbr", "tok_in": 1200, "tok_out": 340,
             "latency_ms": 41200, "status": "success", "cache_hit": "True"}]
    out = _shape_recent(rows)
    r = out["recent"][0]
    assert r["time"] == "2026-06-19T18:42:03" and r["provider"] == "deepinfra"
    assert r["cache_hit"] is True and r["status"] == "success" and r["latency_ms"] == 41200

def test_shape_recent_cache_false_and_none():
    rows = [{"time": _dt(2026,6,19,1,0), "model":"m","provider":"groq","key":"k","tok_in":1,
             "tok_out":2,"latency_ms":500,"status":"success","cache_hit":"False"},
            {"time": _dt(2026,6,19,1,1), "model":"m","provider":"groq","key":"k","tok_in":1,
             "tok_out":2,"latency_ms":500,"status":"failure","cache_hit":None}]
    out = _shape_recent(rows)
    assert out["recent"][0]["cache_hit"] is False and out["recent"][1]["cache_hit"] is None
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Add `_shape_recent` + the endpoint** to `usage_routes.py`:
```python
def _cache_bool(v):
    if v in ("True", "true"): return True
    if v in ("False", "false"): return False
    return None

def _shape_recent(rows):
    return {"recent": [{"time": r["time"].isoformat(), "model": r["model"], "provider": r["provider"] or "",
                        "key": r["key"], "tok_in": r["tok_in"] or 0, "tok_out": r["tok_out"] or 0,
                        "latency_ms": r["latency_ms"] or 0, "status": r["status"] or "",
                        "cache_hit": _cache_bool(r["cache_hit"])} for r in rows]}

@router.get("/usage/recent", dependencies=[Depends(login_required)])
async def usage_recent(limit: int = 50):
    limit = max(1, min(int(limit), 200))
    dsn = get_settings().database_url
    if not dsn:
        return _shape_recent([])
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            'SELECT l."startTime" time, l.model, l.custom_llm_provider provider, '
            'COALESCE(v.key_alias, LEFT(l.api_key,10)) key, l.prompt_tokens tok_in, '
            'l.completion_tokens tok_out, l.request_duration_ms latency_ms, l.status, l.cache_hit '
            'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token=l.api_key '
            'ORDER BY l."startTime" DESC LIMIT $1', limit)
    except Exception:
        log.exception("usage_recent query failed")
        out = _shape_recent([]); out["error"] = "query_failed"; return out
    finally:
        await conn.close()
    return _shape_recent([dict(r) for r in rows])
```
- [ ] **Step 4: Run → PASS; full suite green.** Verify on host: the recent query returns rows.
- [ ] **Step 5: Commit** `git add ui/app/routes/usage_routes.py ui/tests/test_usage_summary.py && git commit -m "feat(ui): /api/usage/recent — metadata-only activity feed"`

---

## Task 3: Frontend — uPlot `Chart.svelte` wrapper

**Files:** Create `ui/frontend/src/lib/Chart.svelte`. Modify `ui/frontend/package.json` (dep).

**Interfaces:**
- Produces: `<Chart {data} {series} {height} />` — `data` is `[xVals, ...ySeriesArrays]` (uPlot AlignedData), `series` is `[{label}, ...]` (one per y-series; index 0 is the x-axis config), `height` px.

- [ ] **Step 1: Add the dependency.** `cd ui/frontend && npm install uplot`. Confirm `uplot` appears in `package.json` dependencies.
- [ ] **Step 2: Create `ui/frontend/src/lib/Chart.svelte`:**
```svelte
<script>
  import { onMount, onDestroy } from 'svelte'
  import uPlot from 'uplot'
  import 'uplot/dist/uPlot.min.css'
  let { data, series, height = 220 } = $props()
  let el = $state(null), plot = null, w = $state(600)
  function opts() {
    return { width: w, height, series,
      scales: { x: { time: true } },
      axes: [{}, {}],
      legend: { live: true } }
  }
  onMount(() => {
    plot = new uPlot(opts(), data, el)
    const ro = new ResizeObserver(() => { w = el.clientWidth; plot?.setSize({ width: w, height }) })
    ro.observe(el)
    return () => ro.disconnect()
  })
  onDestroy(() => plot?.destroy())
  // re-feed data on change
  $effect(() => { if (plot && data) plot.setData(data) })
</script>
<div bind:this={el} bind:clientWidth={w} class="chart"></div>
<style>.chart{width:100%}</style>
```
(uPlot `series` config: element 0 is the x-axis, elements 1..n are y-series with `{label, stroke, scale, value}`. The Usage screen passes ready-made series configs — see Task 4.)
- [ ] **Step 3: Build** `cd ui/frontend && npm run build` → succeeds (uPlot resolves, no SSR issues). Commit `git add ui/frontend/package.json ui/frontend/package-lock.json ui/frontend/src/lib/Chart.svelte && git commit -m "feat(ui): uPlot Chart.svelte wrapper"`

---

## Task 4: Frontend — `Usage.svelte` dashboard rework

**Files:** Modify `ui/frontend/src/routes/Usage.svelte`. **READ it first** (v3.8: `days`/`refreshSec` state from localStorage, `load()`, range buttons, auto-refresh timer, `{#if d?.error}` banner, pause-on-hidden — KEEP all of it).

**Interfaces:**
- Consumes: `/api/usage/summary?days=N` (Task 1 shape), `/api/usage/recent?limit=50` (Task 2 shape), `<Chart>` (Task 3).

- [ ] **Step 1:** Keep the v3.8 scaffolding (range/refresh/persistence/visibility). Extend the range buttons to `[1, 7, 30, 90]` rendering labels `24h/7d/30d/90d` (1 → "24h"). In `load()`, fetch BOTH endpoints in parallel: `const [d, rec] = await Promise.all([api.get(\`/api/usage/summary?days=${days}\`), api.get('/api/usage/recent?limit=50')])`; store `summary = d`, `recent = rec.recent`.
- [ ] **Step 2: KPI row.** Above the charts, render a flex row of stat cards from `summary.kpis`: Spend (`$d.toFixed(4)`), Requests (`toLocaleString`), Tokens (`tok_in` ▸ in / `tok_out` ▸ out), Error rate (`(error_rate*100).toFixed(1)%`, red if >0), Avg latency + p95 latency (`fmtMs` — `<1000 → '###ms'`, else `'#.#s'`), Cache-hit (`cache_hit_rate==null ? '—' : (rate*100).toFixed(0)+'%'`). Reuse the existing `.card.stat` style.
- [ ] **Step 3: Charts.** Build uPlot inputs from `summary.timeseries`: x = `bucket` epoch seconds (`new Date(t.bucket).getTime()/1000`), y-series = requests, spend, p95_ms. `series=[{}, {label:'Requests',stroke:'#0a84ff'}, {label:'Spend $',stroke:'#34c759',scale:'$'}, {label:'p95 ms',stroke:'#ff9f0a',scale:'ms'}]`; `data=[xs, reqs, spends, p95s]`. Render `<Chart {data} {series} height={240} />`. (Three scales: default for requests, `$` and `ms` as secondary — uPlot auto-ranges per scale.)
- [ ] **Step 4: Breakdown tabs.** A tab group `['By provider','By model','By key']` → `let tab = $state('provider')`; show `summary.by_provider | by_model | by_key`. One reusable table snippet with columns: Label, Requests, Tokens (in/out), Spend, Cost/1M (`cost_per_1m==null?'—':'$'+v.toFixed(4)`), p50 (`fmtMs`), p95 (`fmtMs`), Error% (red if >0). The `(none)` label in by_provider/by_model renders as `failed / no backend` (muted). by_key adds a "Last used" column (only that tab). **Client-side sorting:** clicking a column header sorts the current tab's array (`let sortCol`, `let sortDir`; numeric desc default). 
- [ ] **Step 5: Recent activity table** from `recent`: columns time (`toLocaleTimeString`), model (truncate), provider, key, tok in/out, latency (`fmtMs`), status (✓ green / ✗ red), cache (hit/miss/—). Cap rows at 50.
- [ ] **Step 6:** Keep the `{#if summary?.error}` banner (rename `d`→`summary` consistently). Build `cd ui/frontend && npm run build` → succeeds. Commit `git add ui/frontend/src/routes/Usage.svelte && git commit -m "feat(ui): Usage dashboard — KPIs, charts, by-provider/model/key tabs, recent feed, 24h/hourly"`

---

## Task 5: Virtual-key model-access list — show model names, not UUIDs (folded-in bug)

**Files:** Modify `ui/frontend/src/routes/Keys.svelte`. **Independent of Tasks 1–4** (different file) — build in parallel with Task 4.

**Bug:** the create/edit-key "Models (none selected = all)" multi-select lists deployment **UUIDs** (`model_info.id`) instead of public model names, because line ~20 maps the model items by their item key:
```javascript
availableModels = (state.items || []).filter(i => i.kind === 'model').map(i => i.name)   // i.name = UUID
```
Public model names live in `i.data.model_name`, and multiple deployments share one name (the `gpt-oss-20b` group is 3+ items) → must dedupe. LiteLLM's `/key/generate` `models` field also wants public names, so this is functional, not just cosmetic.

- [ ] **Step 1:** READ `Keys.svelte` around line 20 + lines 107–110 (the `<select multiple bind:value={form.models}>` whose `{#each availableModels as m}<option value={m}>{m}</option>` renders both the value sent and the label — so fixing `availableModels` fixes both).
- [ ] **Step 2:** Replace the `availableModels = …` line with the deduped public-name list:
```javascript
      availableModels = [...new Set(
        (state.items || [])
          .filter(i => i.kind === 'model')
          .map(i => i.data?.model_name)
          .filter(Boolean)
      )].sort()
```
- [ ] **Step 3:** Build `cd ui/frontend && npm run build` → succeeds. Commit `git add ui/frontend/src/routes/Keys.svelte && git commit -m "fix(ui): virtual-key model-access list shows model names, not deployment UUIDs"`

---

## Task 6: Integration verification + release

- [ ] **Step 1:** Local-build stack (`docker-compose.override.yml` → `build: ./ui`); seed config; `docker compose up -d --build --wait`; **point the UI's `DATABASE_URL` at a DB with real spend logs** OR run Playwright against the host UI directly. Simplest: verify the new endpoints against the **host** (`http://10.0.20.75:8081`, login `jammer75`) since it has 10K+ real rows. Playwright on **`http://10.0.20.85:8081`** for the local build's screen chrome; data assertions against the host.
- [ ] **Step 2 — endpoints:** `curl` (with login cookie) `http://10.0.20.75:8081/api/usage/summary?days=30` → assert `kpis`, `by_provider` (≥3 rows incl. deepinfra/custom_openai/groq), `timeseries` (non-empty), no `error`. `?days=1` → `granularity:"hour"`. `/api/usage/recent?limit=50` → 50 rows with provider/status/cache.
- [ ] **Step 3 — screen (Playwright, LAN-IP):** KPI row populated; charts render (uPlot canvas present); switch tabs (provider/model/key) — tables populate; click a column header — sorts; recent-activity table has rows; switch 30d→24h — charts re-fetch (hourly); reload page — range + auto-refresh persist (v3.8). Screenshot `docs/images/v39-usage-dashboard.png`.
- [ ] **Step 3b — Keys fix (Task 5):** open Virtual Keys → Create key → the **Models** multi-select shows **public names** (e.g. `gpt-oss-20b`), **deduped** (one entry, not 3 UUIDs); select one + create → the key list shows the model **name**; LiteLLM accepts it (no error).
- [ ] **Step 4:** Full backend suite green; teardown; restore `config/config.yaml`; remove override; `git status` clean.
- [ ] **Step 5 — release:** merge `v3.9-usage-dashboard` → `main` (`--no-ff`), push → CI cuts **`1.19.0`** + image; bump compose/admin-ui pin to `1.19.0` (rebase past the release commit); push.

## Self-Review
- **Spec coverage:** KPIs+breakdowns+timeseries → T1; recent feed → T2; chart lib → T3; dashboard screen (KPI/charts/tabs/feed/24h) → T4; folded-in key-model-name bug → T5; integration+release → T6. ✓
- **Type consistency:** `_shape_summary(days,granularity,kpis,by_provider,by_model,by_key,timeseries)` (T1) ↔ endpoint call; `_cols`/`_ms` helpers; `_shape_recent(rows)` (T2); `<Chart {data} {series} {height}>` (T3) ↔ Usage.svelte usage (T4). ✓
- **Placeholders:** backend code + SQL + tests complete; chart wrapper complete; Usage.svelte steps give exact data wiring, columns, formatters, sort logic + reuse standard markup. The cache_hit metric is an explicit T1-Step-6 resolution, not a placeholder. ✓
- **Risk flagged:** by_key SQL string-munging + FILTER-on-percentile_cont verified on host in T1 Step 6 (adjust to a CTE if PG rejects FILTER on the ordered-set aggregate). ✓
