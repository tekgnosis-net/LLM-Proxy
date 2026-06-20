# LLM-Proxy Admin UI — Richer Usage & Spend Dashboard (Design)

**Status:** design (brainstormed 2026-06-20). Builds on shipped v3.8.1 (`1.18.1`). Branch: `v3.9-usage-dashboard`. Releases as `1.19.0`.

**Why:** the Usage & Spend screen is too basic — it uses only 5 of `LiteLLM_SpendLogs`' 32 columns (`spend`, `total_tokens`, `model`, `api_key`, `startTime`) → totals + by-model + by-key + daily bars. The table carries far richer, *populated* operational data the screen ignores: per-request **latency** (`request_duration_ms`, 96% populated), **TTFT** (`completionStartTime`), **prompt/completion token split**, **status** (success/failure), **cache_hit**, and **provider/deployment** (`custom_llm_provider`, `api_base`, `model_group`). Goal: a **balanced operational + cost dashboard**.

**Decided in brainstorming:** balanced dashboard (not cost-only or perf-only); a small **chart library** (uPlot) for real time-series; activity feed is **metadata-only** (no message-content inspector). Skip per-user/per-team/call-type breakdowns (no usable data — 1 user, ~2 teams, all `acompletion`).

---

## 1. Architecture

**Backend (`ui/app/routes/usage_routes.py`):**
- **Extend** `GET /api/usage/summary?days=N` to return the full dashboard payload (KPIs + breakdowns + time-series). Granularity is derived from the range: `days <= 2 → hourly` buckets, else `daily`.
- **Add** `GET /api/usage/recent?limit=N` (default 50, cap 200) for the activity feed — row-level, a different shape, so its own endpoint (and the feed can refresh on its own cadence / lazy-load).
- All SQL on `LiteLLM_SpendLogs` via the existing `asyncpg.connect(dsn)` pattern. Keep the v3.7.1 guard: on query failure, **log loudly + return `{error:"query_failed"}`** (never silent zeros).
- **Pure shape helpers** (`_shape_summary`, `_shape_recent`) take query rows → dict — TDD'd without a DB (the existing pattern). SQL execution is integration-verified against host data.

**Frontend (`ui/frontend/src/routes/Usage.svelte` + a chart wrapper):**
- Rework `Usage.svelte` into a dashboard. Add **uPlot** (`npm i uplot`, ~40KB, canvas, framework-agnostic) wrapped in a small Svelte 5 `Chart.svelte` (mount in `onMount`, destroy in `onDestroy`, re-render on data change).
- **Preserve** the v3.8 range selector + **saved auto-refresh** + `localStorage` persistence (`usage.days`, `usage.refreshSec`); **add a `24h` range** (→ hourly charts).

**Performance:** aggregations run over the `startTime`-indexed table; exact `percentile_cont` is sub-second at current scale (~10K rows/30d). Note (not implement): at millions of rows, switch to approximate percentiles or pre-aggregation.

---

## 2. Layout

```
┌ Usage & Spend ─────────────────────────────[ 24h · 7d · 30d · 90d ]·[↻ auto: off/10s/30s/60s/5m]┐
│ KPI ROW:  Spend · Requests · Tokens(in/out) · Error% · Avg latency · p95 latency · Cache-hit% │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ CHARTS (uPlot, multi-series; daily, or hourly for 24h):                                        │
│   requests ─── · spend ─── · p95 latency ───      (hover tooltip: date · reqs · $ · p95)        │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ BREAKDOWNS   [ By deployment/provider | By model | By key ]   (tab group)                       │
│   columns: requests · tokens in/out · spend · cost/1M · p50 · p95 · error%   (sortable)         │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ RECENT ACTIVITY (last 50):                                                                     │
│   time · model · provider · key · tok in/out · latency · status(✓/✗) · cache(hit/miss)          │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Metrics & data (all from verified-populated columns)

**KPIs** (single row over the range):
- `spend`, `requests`, `prompt_tokens` (in), `completion_tokens` (out).
- **error rate** = `COUNT(*) FILTER (WHERE status='failure') / COUNT(*)`.
- **avg latency** = `AVG(request_duration_ms)`; **p95 latency** = `percentile_cont(0.95)`.
- **cache-hit %** = `COUNT(*) FILTER (WHERE cache_hit IN ('True','true')) / COUNT(*)` — **NOTE:** verify LiteLLM's cache_hit semantics during build; the naive `True/(True+False)` gave a bogus 100% because most rows are `None`. Use `hits ÷ total`; if that's also misleading, drop the metric or label it precisely.

**Breakdown tables** — three tabs, same columns, different `GROUP BY`:
- **By provider** (`custom_llm_provider`) — *the headline view* (vLLM/deepinfra/groq split).
- **By model** (`model` path; `model_group` available if multiple groups exist).
- **By key** (`api_key` → `key_alias` via `LiteLLM_VerificationToken`, fallback `LEFT(api_key,10)`; + `last_used`).
- Columns: `requests`, `tok_in`, `tok_out`, `spend`, **`cost_per_1m`** = `SUM(spend)/NULLIF(SUM(total_tokens),0)*1e6`, **`p50_ms`**, **`p95_ms`**, **`err_pct`**. Sortable client-side.
- **Failures handling:** `status='failure'` rows have no provider/tokens → a `(none)`/`failed` row. The table labels it `failed (no backend)` and excludes it from cost columns; the headline signal is the **error% KPI**.

**Verified query (by-provider, the core)** — runs and returns sane data on the host:
```sql
SELECT COALESCE(NULLIF(custom_llm_provider,''),'(none)') AS provider,
  COUNT(*) requests, SUM(prompt_tokens) tok_in, SUM(completion_tokens) tok_out,
  SUM(spend) spend, SUM(spend)/NULLIF(SUM(total_tokens),0)*1e6 cost_per_1m,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY request_duration_ms) p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY request_duration_ms) p95_ms,
  100.0*COUNT(*) FILTER (WHERE status='failure')/COUNT(*) err_pct
FROM "LiteLLM_SpendLogs" WHERE "startTime" > now() - make_interval(days => $1)
GROUP BY provider ORDER BY requests DESC;
```

**Time-series** (`timeseries`): bucket by `date_trunc('day'|'hour', "startTime")` → `requests`, `spend`, `p95_ms` per bucket. Three uPlot series.

**Recent activity** (`/api/usage/recent?limit=N`): `SELECT "startTime", model, custom_llm_provider, api_key→alias, prompt_tokens, completion_tokens, request_duration_ms, status, cache_hit FROM "LiteLLM_SpendLogs" ORDER BY "startTime" DESC LIMIT $1`. No `messages`/`response` (metadata-only).

---

## 4. Endpoint payload shapes

`GET /api/usage/summary?days=N`:
```json
{ "range_days": 30, "granularity": "day",
  "kpis": {"spend": 0.23, "requests": 10605, "tok_in": 16944318, "tok_out": 2755995, "error_rate": 0.032, "avg_latency_ms": 8720, "p95_latency_ms": 57082, "cache_hit_rate": null},
  "by_provider": [{"provider":"deepinfra","requests":3926,"tok_in":2966557,"tok_out":1102246,"spend":0.2187,"cost_per_1m":0.0538,"p50_ms":879,"p95_ms":17886,"err_pct":0.0}],
  "by_model": [ … same column set … ],
  "by_key":   [ … + "last_used" … ],
  "timeseries": [{"bucket":"2026-06-19","requests":412,"spend":0.003,"p95_ms":41000}],
  "error": null }
```
`GET /api/usage/recent?limit=50`:
```json
{ "recent": [{"time":"2026-06-19T18:42:03","model":"deepinfra/openai/gpt-oss-20b","provider":"deepinfra","key":"hindsight-cbr","tok_in":1200,"tok_out":340,"latency_ms":41200,"status":"success","cache_hit":false}] }
```

---

## 5. Build phasing (one branch `v3.9-usage-dashboard`, released `1.19.0`)
1. **Backend** — extend `/api/usage/summary` (KPIs + by_provider/model/key + timeseries; granularity from days) + new `/api/usage/recent`. TDD the pure shape helpers (`_shape_summary` extended, `_shape_recent`); empty-DB → zeroed shape (no 500); query-fail → `{error}`. Verify the SQL against host data (cache-hit semantics resolved here).
2. **Chart wrapper** — add `uplot` dep + `Chart.svelte` (Svelte 5 wrapper: onMount create, $effect setData, onDestroy destroy; responsive width).
3. **Dashboard rework** — `Usage.svelte`: KPI row, charts, tabbed breakdown tables (sortable), recent-activity table; 24h range + hourly; preserve range/auto-refresh persistence + the `{error}` banner.
4. **Integration + release** — local build, Playwright on **`http://10.0.20.85:8081`** (LAN-IP), verify every panel populates from real host data (provider split, latency, errors, feed); screenshots; merge → `1.19.0`; bump pin.

## Out of scope
- Per-user / per-team / call-type breakdowns (no usable data).
- Request/response **content inspector** (metadata-only feed by decision).
- Budget tracking / spend projections (cost is negligible; can revisit).
- CSV/export (can be a later add).

## Testing
- **Backend (TDD):** `_shape_summary` maps KPI/breakdown/timeseries rows incl. cost_per_1m None-guard, err_pct, latency; `_shape_recent` maps feed rows (cache_hit text→bool, status). Empty-DB → zeroed KPIs + empty arrays (no 500). Granularity = hour when days≤2 else day.
- **SQL (integration on host):** the by-provider/model/key, timeseries, and recent queries return sane data; **cache_hit semantics confirmed** (or the metric adjusted/dropped).
- **Frontend (Playwright, LAN-IP):** KPI row + all three breakdown tabs + charts + feed render from real data; sorting works; 24h switches to hourly; range + auto-refresh persist across reload; `{error}` path shows the banner.
