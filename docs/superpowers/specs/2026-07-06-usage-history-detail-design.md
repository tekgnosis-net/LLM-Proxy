# Usage Activity History + Transaction Detail Design

**Status:** Approved (design), 2026-07-06
**Builds on:** the 1.19.0 Usage dashboard (KPI row, uPlot chart, breakdown tabs, recent feed) and the existing `LiteLLM_SpendLogs` SQL layer in `usage_routes.py`.

## Goal

Extend the Usage & Spend screen's activity feed with (1) a **History** view scoped to
the existing 24h/7d/30d/90d range selector, with filters and pagination, and (2) a
**click-through, expand-in-place transaction detail** — per-row spend, cost/1M,
latency (incl. TTFT vs generation split), and full error detail for failures —
improving day-to-day monitoring without adding any new logging.

## Grounding facts (verified on the live host, 2026-07-06)

- `LiteLLM_SpendLogs` has 32 columns. Per-row we already store: `request_id`,
  `call_type`, `api_key`, `spend`, `prompt_tokens`/`completion_tokens`/`total_tokens`,
  `startTime`/`endTime`/`completionStartTime`, `model`, `model_id`, `model_group`,
  `custom_llm_provider`, `api_base`, `metadata` (jsonb), `cache_hit`, `request_tags`,
  `team_id`, `end_user`, `session_id`, `status`, `request_duration_ms`.
- **Prompts/responses are NOT logged**: `messages` and `response` are `{}` unless
  litellm's `store_prompts_in_spend_logs` is enabled. Out of scope by explicit user
  decision — the detail view shows metadata only, no content.
- **Failures carry rich detail**: `metadata.error_information` =
  `{error_class, error_code, error_message, llm_provider, traceback}`.
- `completionStartTime - startTime` ≈ time-to-first-token; `endTime -
  completionStartTime` ≈ generation time. Either may be NULL/equal (failures, old
  rows) — derive defensively.
- Volume on the live host: ~32k rows / ~400 failures per ~4 weeks — keyset pagination
  is ample; no new indexes required at this scale.
- **Percentiles are set-properties, not row-properties.** Design decision: each row
  shows its own latency; the History view shows a percentile strip (p50/p90/p95/p99 +
  err%) computed server-side over exactly the filtered window.

## Architecture (Approach B — component + two endpoints)

A new `ActivityFeed.svelte` component owns the feed (both modes); `Usage.svelte`
hosts it and passes `days`. Two read-only SQL endpoints in `usage_routes.py`, in the
style of the existing ones (asyncpg, parameterized, `query_failed` guard). The old
`GET /api/usage/recent` is replaced by `GET /api/usage/activity` (only our UI calls
it).

### 1. `GET /api/usage/activity` (replaces `/usage/recent`)

Query params:
- `days: int` (1|7|30|90, clamp 1..365) — window `startTime > now() - make_interval(days => $n)`; **always applied** (Recent mode simply uses it with default paging).
- `status: all|success|failure` (default all) — `failure` ⇒ `l.status='failure'`; `success` ⇒ `l.status IS DISTINCT FROM 'failure'` (older success rows may have NULL status).
- `model: str` (optional) — matches `l.model_group` if set else `l.model` (same labeling as the by_model breakdown).
- `key: str` (optional) — matches the by_key label: `COALESCE(v.key_alias, LEFT(l.api_key,10))`.
- `limit: int` (default 50, max 200).
- `cursor: str` (optional) — **keyset cursor**, opaque `"<iso_ts>|<request_id>"` of the last row seen; page condition `(l."startTime", l.request_id) < ($ts, $id)` with `ORDER BY l."startTime" DESC, l.request_id DESC`. Stable under concurrent inserts.
- `stats: 0|1` (default 0) — when 1, ALSO return the percentile strip: one extra query sharing the identical WHERE: `percentile_cont(ARRAY[0.5,0.9,0.95,0.99]) WITHIN GROUP (ORDER BY request_duration_ms) FILTER (WHERE request_duration_ms>0)`, plus count + err%.

Response:
```json
{ "rows": [ { "id": "<request_id>", "time": "<iso+00:00>", "model": "...",
    "provider": "...", "key": "...", "tok_in": 0, "tok_out": 0, "spend": 0.0,
    "latency_ms": 0, "status": "success|failure", "cache_hit": true|false|null,
    "call_type": "..." } ],
  "next_cursor": "<ts|id> | null",
  "stats": { "count": N, "err_pct": 0.0, "p50_ms": 0, "p90_ms": 0, "p95_ms": 0, "p99_ms": 0 } // only when stats=1
}
```
All timestamps `_iso_utc` (browser-local rendering, per the 1.23.0 timezone rule).
`rows` is lean — detail comes from the tx endpoint. Empty `database_url` → same
empty-shape guard as today.

### 2. `GET /api/usage/tx/{request_id}`

Returns one transaction's **allowlisted** detail (never `messages`, `response`,
`proxy_server_request`, raw `metadata`):

```json
{ "id", "time", "end_time", "call_type", "status", "cache_hit",
  "model_group", "model", "model_id", "provider", "api_base",
  "key": "<alias-or-prefix>", "team_id", "end_user", "session_id",
  "tags": [...],
  "tok_in", "tok_out", "tok_total", "spend", "cost_per_1m",   // spend/total*1e6, null when no tokens
  "latency_ms", "ttft_ms", "gen_ms",                          // ttft/gen null when completionStartTime is NULL or out of order
  "error": { "class", "code", "message", "provider", "traceback" } // null unless failure; traceback truncated to 4000 chars
}
```
404 if `request_id` not found. Shaping in pure functions (`_shape_tx`,
`_extract_error(metadata)`) for unit-testability.

### 3. `ActivityFeed.svelte` (new component; `Usage.svelte` hosts it)

- Props: `days` (from the existing range selector — History always follows it),
  plus a `refreshTick` signal so Recent mode participates in the existing silent
  auto-refresh.
- **Header switcher** `Recent | History` — segmented control styled like the range
  buttons; persisted in `localStorage['usage.activityMode']`.
- **Recent mode** (default): current behavior — newest 50 in the window, silently
  reloaded by the auto-refresh timer; no cursor, no filters, no strip.
- **History mode**: filter chips row — Status (All/Success/Failure) as three-way
  segmented chip; Model and Key as `<select>`s populated from the summary's
  `by_model`/`by_key` labels (passed down from Usage.svelte; no extra fetch).
  Percentile strip (from `stats=1` on the first page) rendered as small stat pills:
  `n requests · err % · p50 · p90 · p95 · p99` — recomputed whenever
  days/filters change. List renders pages appended via **Load more** (uses
  `next_cursor`; button hidden when null). Auto-refresh does NOT reload History
  (no scroll rug-pulls); changing days/filters resets the list.
- **Row rendering** (both modes): time (local), model, provider, key, tok in/out,
  spend (4dp), latency (ms/s auto-format), status. Failure rows: subtle red tint +
  "failed" chip (reuse `.banner.err` palette tokens).
- **Expand-in-place detail** (both modes): clicking a row toggles an accordion
  panel; first expand lazy-fetches `/api/usage/tx/{id}` and caches it on the row
  (subsequent toggles are instant). Panel layout:
  - a definition grid (2-col): request-id with Copy button (`copyText` from
    `lib/browser.js`), call type, provider + api_base, model group → model
    (+ model_id), tokens in/out/total, spend + cost/1M, cache hit, session,
    end-user, tags;
  - a **timing line**: `TTFT {x} · generation {y} · total {z}` (segments omitted
    when null);
  - **failure block** when `error` present: class/code/provider + message, and the
    traceback behind a `<details>` collapsible in a scrollable `<pre>`.
  - Loading/error states inside the panel ("Couldn't load detail" + retry).
- Fixed-height scrollable list is NOT introduced — the card grows as today;
  Load more caps growth naturally.

### Files

- Modify: `ui/app/routes/usage_routes.py` (replace `usage_recent` with
  `usage_activity`; add `usage_tx`; pure shapers `_shape_activity_row`,
  `_shape_tx`, `_extract_error`, cursor encode/decode helpers).
- Create: `ui/frontend/src/routes/ActivityFeed.svelte` (goes in `routes/` beside
  the screens it serves; it's screen-scoped, not a lib).
- Modify: `ui/frontend/src/routes/Usage.svelte` (drop the inline feed markup +
  `recent` state; render `<ActivityFeed {days} {byModel} {byKey} refreshTick={...} />`).
- Modify: `ui/frontend/src/lib/api.js` (activity + tx calls).
- Modify: `docs/admin-ui-guide.md` (Usage section: Recent|History subsection).

## Error handling

- Both endpoints keep the loud-log + `{"error":"query_failed"}` pattern (1.17.1
  lesson: never silently return plausible emptiness on DB errors); the component
  shows "couldn't load" distinct from "no activity".
- Malformed cursor → 422. Unknown request_id → 404 → panel shows not-found.
- `error_information` missing/malformed on a failure row → `error` with whatever
  fields exist, never a 500 (defensive `.get()` chain).

## Testing

- **Unit (pytest, existing style):** cursor round-trip + malformed-cursor rejection;
  WHERE-builder for each filter combination (parameterized, no SQL injection via
  model/key strings); `_shape_tx` (cost/1M math incl. zero-token null, ttft/gen
  derivation incl. NULL/inverted timestamps); `_extract_error` (full, partial,
  absent, traceback truncation at 4000).
- **Route tests (fake asyncpg, existing harness):** activity pagination
  (next_cursor emitted/consumed), stats block only when requested, tx 404,
  DATABASE_URL-empty guards.
- **Playwright (local stack):** seed ~8 synthetic SpendLogs rows via SQL (mixed
  models/keys, one failure with error_information, one with completionStartTime)
  → verify: switcher persists; History follows the range selector; filters narrow
  the list + strip updates; Load more appends (seed > page size with limit=5);
  expand shows detail incl. cost/1M + timing; failure row shows error + traceback;
  Recent mode still auto-refreshes.

## Out of scope (YAGNI)

- Prompt/response capture (`store_prompts_in_spend_logs`) — explicitly declined.
- A separate Transactions page, CSV export, per-row percentile-rank badges,
  cross-linking from breakdown tabs into pre-filtered history (possible later; the
  filter model supports it).
- Retention/housekeeping changes — history depth remains whatever
  `HOUSEKEEPING_SPENDLOG_RETENTION_DAYS` keeps (90d default aligns with the max
  range button).
