# LLM-Proxy Admin UI — v3.7 Design: health-dot fix, richer Usage, live logs + Debug toggle

**Status:** design (brainstormed 2026-06-10). Builds on shipped v3.6 (`1.16.0`). Branch: `v3.7-observability`. Releases as `1.17.0`.

**Why:** UI-testing feedback. (a) The model health dots are always grey — a real mapping bug. (b) The Usage screen is too bleak — spend-by-model / spend-by-key / daily requests need real detail. (c) No way to watch the router work; the user wants a live LiteLLM log view + a way to raise the log level (this is what would have made the cooldown/"rate-limit" hunt self-serve).

Theme: **observability**. Three independent pieces.

---

## 1. Health-dot fix (Models)

**Bug:** LiteLLM `GET /health` returns each deployment keyed by **`model_id`** (= the `model_info.id` UUID we materialize since 1.15.0) and the litellm `model` path — but **not** the public model name. `Models.svelte` builds `healthMap` keyed by `ep.model ?? ep.model_name` (the litellm path, e.g. `groq/openai/gpt-oss-20b`) and looks it up by `item.data.model_name` (`gpt-oss-20b`). Keys never match → every dot is grey ("pending").

**Fix (frontend-only, `Models.svelte`):**
- Build the map keyed by the deployment UUID: `for (ep of healthy_endpoints) map[ep.model_id] = true` (and `false` for `unhealthy_endpoints`).
- Look it up by the model item's `name` (which *is* the UUID = `model_info.id`): in `healthInfo(item)`, use `healthMap[item.name]` instead of `healthMap[item.data.model_name]`.
- Keep the existing fallbacks (`flag === 'new'` → "Not applied yet"; otherwise "pending").

A deployment with no applied `model_info.id` yet (brand-new, pre-Apply) stays grey — correct, since `/health` can't report it. Confirmed against the live host: `/health` returns `healthy_endpoints[].model_id` = our UUIDs.

---

## 2. Richer Usage

**Problem:** the Usage screen shows shallow spend-by-model / spend-by-key / daily numbers. Make each detailed.

**Data source — SQL on `LiteLLM_SpendLogs`** (the UI already holds `DATABASE_URL`; richer + more reliable than `/global/spend/*`). Relevant columns: `spend`, `total_tokens`, `model`, `api_key` (hashed token), `startTime`. Key aliases come from joining `LiteLLM_VerificationToken (token, key_alias, key_name)` on `api_key = token`.

**Backend — one new endpoint `GET /api/usage/summary?days=N` (`login_required`)**, reusing the existing asyncpg connection helper. Returns:
```json
{
  "range_days": 30,
  "totals": {"spend": 1.23, "requests": 456, "tokens": 789000},
  "by_model": [{"model": "gpt-oss-20b", "spend": 1.0, "requests": 400, "tokens": 700000}, ...],
  "by_key":   [{"key": "<alias or short-hash>", "spend": 0.2, "requests": 56, "last_used": "2026-06-10T.."}, ...],
  "daily":    [{"day": "2026-06-09", "requests": 30, "spend": 0.1}, ...]
}
```
Queries (all `WHERE "startTime" > now() - ($1 || ' days')::interval`):
- **totals:** `SELECT COALESCE(SUM(spend),0), COUNT(*), COALESCE(SUM(total_tokens),0) FROM "LiteLLM_SpendLogs" WHERE ...`
- **by_model:** `... SELECT model, SUM(spend) s, COUNT(*) r, SUM(total_tokens) t ... GROUP BY model ORDER BY s DESC`
- **by_key:** `SELECT COALESCE(v.key_alias, LEFT(l.api_key, 10)) k, SUM(l.spend) s, COUNT(*) r, MAX(l."startTime") last FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token = l.api_key WHERE ... GROUP BY k ORDER BY s DESC`
- **daily:** `SELECT date_trunc('day', "startTime")::date d, COUNT(*) r, SUM(spend) s ... GROUP BY d ORDER BY d`

Guard: if `LiteLLM_SpendLogs` is empty/absent, return zeroed totals + empty arrays (no 500).

**Frontend — `Usage.svelte` rework:** a range selector (7 / 30 / 90 days), a totals header (spend / requests / tokens), two sortable tables (by model, by key), and a simple daily bar row (CSS bars from `daily`). Reuse existing table/card styles.

---

## 3. Live logs + Debug toggle (new Logs screen)

**Backend — `GET /api/logs/stream` (SSE, `login_required`)** in a new `ui/app/routes/logs_routes.py`:
- Opens a streaming GET against the socket-proxy:
  `GET {SOCKET_PROXY_URL}/containers/{LITELLM_CONTAINER}/logs?follow=1&stdout=1&stderr=1&tail={tail}&timestamps=1` via `httpx.AsyncClient().stream(...)`.
- **De-multiplex the Docker stream.** A non-TTY container's log stream is framed: each frame = an 8-byte header `[stream_type(1B), 0,0,0, size(4B big-endian)]` followed by `size` payload bytes. Buffer `aiter_bytes()` chunks; repeatedly parse `header(8) + payload(size)`; split payloads into lines; emit each line as SSE `data: <line>\n\n`. (Without de-framing, the browser sees binary header bytes — this is the one fiddly part.)
- Wrap in `StreamingResponse(gen(), media_type="text/event-stream")` with `Cache-Control: no-cache` + `X-Accel-Buffering: no`. On client disconnect / upstream error, close the httpx stream cleanly (no traceback spam).
- `tail` query param (default 200, cap 1000) for the initial backfill.

**Socket-proxy permission — verify-point:** the restart path already uses the proxy with `CONTAINERS=1` + `POST=1`; `GET /containers/{id}/logs` is under `CONTAINERS`, so it should serve. If `tecnativa/docker-socket-proxy` returns 403 for `/logs`, add the documented allow-env to the `socket-proxy` service in `docker-compose.yml` and `.env.example`. Confirm during integration.

**Debug toggle — config-backed (`set_verbose`).** LiteLLM's proxy verbose logging = `litellm_settings: { set_verbose: true }` in config.yaml (confirmed via LiteLLM docs; the SDK's `litellm.set_verbose` maps to this config key). The v3 model already manages `litellm_setting` items, so the toggle:
- reads current `set_verbose` from the effective config (off if absent),
- on flip, `stageItem('litellm_setting', 'set_verbose', <bool>)` then **Apply** (restart) — behind a confirm: *"Raising the log level restarts LiteLLM (~20s) and drops in-flight requests. Continue?"*
- `set_verbose` is boolean (verbose on/off), which is what's needed to see routing/cooldown decisions. (Granular `LITELLM_LOG` levels are env-only and out of the config model — noted, not used.)

**Frontend — new `Logs.svelte` screen** (sidebar under "System"): a live panel using `EventSource('/api/logs/stream?tail=200')` that appends lines with **auto-scroll**, **Pause** (closes/reopens the EventSource), and **Clear** (empties the buffer; cap the in-DOM buffer at ~2000 lines to bound memory). A **"Debug logging"** toggle wired to the `set_verbose` flow above, with the restart confirm.

**Security:** admin-only (`login_required`). A one-line UI note: "Debug logs can include request content; visible to admins only." The admin already holds the master key + all secrets, so this is acceptable exposure — no new secret surface.

---

## Build phasing (one branch `v3.7-observability`, released `1.17.0`)
1. **Health-dot fix** (#1) — `Models.svelte` mapping. Smallest; ships value immediately.
2. **Richer Usage** (#2) — backend `/api/usage/summary` (TDD the SQL-shaping with a fake/seeded query layer), then `Usage.svelte`.
3. **Live logs + Debug toggle** (#3) — backend SSE + de-mux (TDD the de-framer as a pure function), socket-proxy verify, then `Logs.svelte` + the `set_verbose` toggle.
4. **Integration + release** — Playwright on the LAN-IP origin: health dots go green after Apply; Usage shows real grouped numbers over a range; Logs streams live and the Debug toggle restarts + raises verbosity. Merge → `1.17.0`, bump pin.

## Out of scope
- Granular per-level log control (`LITELLM_LOG` levels) — env-only; the boolean `set_verbose` toggle suffices.
- Log download/export, server-side log retention/search (the stream is ephemeral, tail-based).
- Per-user/team spend breakdown beyond by-key (YAGNI for now).
- Charting libraries — daily bars are CSS, no dependency.

## Testing
- **#1:** manual/Playwright — after Apply, a healthy deployment's dot is green and its tooltip reads "Healthy" (was grey). (Pure frontend mapping; verified via the live `/health` shape.)
- **#2 (TDD):** the summary builder returns the documented shape from seeded rows; empty `LiteLLM_SpendLogs` → zeroed totals + empty arrays (no 500); by_key falls back to a short hash when no alias.
- **#3 (TDD):** the **de-framer** is a pure function — feed it concatenated Docker frames (8-byte header + payload, incl. a split-across-chunks frame) → it yields the correct decoded lines. Integration: `/api/logs/stream` emits SSE lines from the live container; the Debug toggle stages `set_verbose` + Applies; socket-proxy serves `/logs` (or the allow-env is added).
