# LLM-Proxy Admin UI — v3.7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Backend = TDD (`cd ui && .venv/bin/python -m pytest -q`). Frontend (Svelte 5) = build (`cd ui/frontend && npm run build`) + Playwright on a **LAN-IP origin** (`http://10.0.20.85:8081`, NOT localhost). Steps use `- [ ]`. **Branch: `v3.7-observability`** (already created).

**Goal:** Fix the always-grey model health dots, give the Usage screen real detail, and add a live LiteLLM log view with a Debug-logging toggle.

**Architecture:** Three independent pieces. (1) Frontend-only health-map re-key by deployment UUID. (2) A new SQL-backed `/api/usage/summary` (shaped by a pure, TDD'd function) + a reworked Usage screen. (3) A new SSE `/api/logs/stream` that de-frames Docker's multiplexed log stream (the de-framer is a pure, TDD'd function) + a Logs screen with a `set_verbose` Debug toggle.

**Tech Stack:** FastAPI, asyncpg, httpx (streaming), Svelte 5 runes, docker-socket-proxy, SSE.

**Spec:** [`../specs/2026-06-10-llm-proxy-ui-v3.7-design.md`](../specs/2026-06-10-llm-proxy-ui-v3.7-design.md).

---

## File Structure
```
ui/frontend/src/routes/Models.svelte      # MODIFY: health map keyed by model_id, lookup by item.name
ui/app/routes/usage_routes.py             # MODIFY: add /api/usage/summary + _shape_summary helper
ui/tests/test_usage_summary.py            # CREATE: TDD _shape_summary
ui/frontend/src/routes/Usage.svelte       # MODIFY: range selector, totals, tables, daily bars
ui/app/routes/logs_routes.py              # CREATE: SSE /api/logs/stream + deframe_docker_log helper
ui/tests/test_logs_deframe.py             # CREATE: TDD the de-framer
ui/app/main.py                            # MODIFY: register logs_routes router
ui/frontend/src/routes/Logs.svelte        # CREATE: live log panel + Debug toggle
ui/frontend/src/App.svelte (or router)    # MODIFY: add Logs to sidebar (System group)
```

---

## Task 1: Health-dot fix (Models.svelte)

**Files:** Modify `ui/frontend/src/routes/Models.svelte`. **READ the `onMount` health-load block + `healthInfo(item)`.**

- [ ] **Step 1:** In `onMount`, change the health-map keys from the litellm model path to the deployment UUID. Replace the two loops:
```javascript
      for (const ep of (h.healthy_endpoints ?? [])) {
        if (ep.model_id) map[ep.model_id] = true
      }
      for (const ep of (h.unhealthy_endpoints ?? [])) {
        if (ep.model_id) map[ep.model_id] = false
      }
```
- [ ] **Step 2:** In `healthInfo(item)`, look up by the item's UUID name (which equals `model_info.id`): change `const st = healthMap[item.data.model_name]` to `const st = healthMap[item.name]`. Leave the rest (`true`→green/Healthy, `false`→red/Unhealthy, `flag==='new'`→grey/"Not applied yet", else grey/"pending") unchanged.
- [ ] **Step 3:** Build `cd ui/frontend && npm run build` → succeeds. Commit `git add ui/frontend/src/routes/Models.svelte && git commit -m "fix(ui): map model health by deployment id (model_info.id), not public name — dots were always grey"`

---

## Task 2: Richer Usage

### Part A — backend `/api/usage/summary` (TDD the shaper)
**Files:** Modify `ui/app/routes/usage_routes.py`. Create `ui/tests/test_usage_summary.py`.

- [ ] **Step 1: Failing test** — `ui/tests/test_usage_summary.py` (match the repo's test style):
```python
from datetime import datetime
from app.routes.usage_routes import _shape_summary

def test_shape_summary_maps_rows():
    totals = {"spend": 1.5, "requests": 10, "tokens": 2000}
    by_model = [{"model": "gpt-oss-20b", "s": 1.5, "r": 10, "t": 2000}]
    by_key = [{"k": "team-a", "s": 1.0, "r": 6, "last": datetime(2026, 6, 10, 9, 0)},
              {"k": "abcd012345", "s": 0.5, "r": 4, "last": None}]
    daily = [{"d": datetime(2026, 6, 9).date(), "r": 4, "s": 0.5},
             {"d": datetime(2026, 6, 10).date(), "r": 6, "s": 1.0}]
    out = _shape_summary(30, totals, by_model, by_key, daily)
    assert out["range_days"] == 30
    assert out["totals"] == {"spend": 1.5, "requests": 10, "tokens": 2000}
    assert out["by_model"][0] == {"model": "gpt-oss-20b", "spend": 1.5, "requests": 10, "tokens": 2000}
    assert out["by_key"][0]["key"] == "team-a" and out["by_key"][1]["last_used"] is None
    assert out["daily"][0]["day"] == "2026-06-09"

def test_shape_summary_handles_empty():
    out = _shape_summary(7, {"spend": None, "requests": 0, "tokens": None}, [], [], [])
    assert out["totals"] == {"spend": 0.0, "requests": 0, "tokens": 0}
    assert out["by_model"] == [] and out["by_key"] == [] and out["daily"] == []
```
- [ ] **Step 2: Run → FAIL** (`_shape_summary` undefined). `cd ui && .venv/bin/python -m pytest tests/test_usage_summary.py -v`.
- [ ] **Step 3: Add `_shape_summary`** to `usage_routes.py`:
```python
def _shape_summary(days, totals, by_model, by_key, daily):
    return {
        "range_days": days,
        "totals": {"spend": float(totals.get("spend") or 0), "requests": totals.get("requests") or 0,
                   "tokens": totals.get("tokens") or 0},
        "by_model": [{"model": r["model"], "spend": float(r["s"] or 0), "requests": r["r"],
                      "tokens": r["t"] or 0} for r in by_model],
        "by_key": [{"key": r["k"], "spend": float(r["s"] or 0), "requests": r["r"],
                    "last_used": r["last"].isoformat() if r["last"] else None} for r in by_key],
        "daily": [{"day": r["d"].isoformat(), "requests": r["r"], "spend": float(r["s"] or 0)} for r in daily],
    }
```
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Add the endpoint** to `usage_routes.py` (asyncpg, mirrors `config_db`'s `asyncpg.connect`):
```python
import asyncpg

@router.get("/usage/summary", dependencies=[Depends(login_required)])
async def usage_summary(days: int = 30):
    days = max(1, min(int(days), 365))
    dsn = get_settings().database_url
    if not dsn:
        return _shape_summary(days, {"spend": 0, "requests": 0, "tokens": 0}, [], [], [])
    win = f"{days} days"
    conn = await asyncpg.connect(dsn)
    try:
        totals = await conn.fetchrow(
            'SELECT COALESCE(SUM(spend),0) spend, COUNT(*) requests, COALESCE(SUM(total_tokens),0) tokens '
            'FROM "LiteLLM_SpendLogs" WHERE "startTime" > now() - $1::interval', win)
        by_model = await conn.fetch(
            'SELECT model, SUM(spend) s, COUNT(*) r, SUM(total_tokens) t FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" > now() - $1::interval GROUP BY model ORDER BY s DESC NULLS LAST LIMIT 50', win)
        by_key = await conn.fetch(
            'SELECT COALESCE(v.key_alias, LEFT(l.api_key,10)) k, SUM(l.spend) s, COUNT(*) r, MAX(l."startTime") last '
            'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token = l.api_key '
            'WHERE l."startTime" > now() - $1::interval GROUP BY k ORDER BY s DESC NULLS LAST LIMIT 50', win)
        daily = await conn.fetch(
            'SELECT date_trunc(\'day\', "startTime")::date d, COUNT(*) r, SUM(spend) s FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" > now() - $1::interval GROUP BY d ORDER BY d', win)
    except asyncpg.PostgresError:
        return _shape_summary(days, {"spend": 0, "requests": 0, "tokens": 0}, [], [], [])
    finally:
        await conn.close()
    return _shape_summary(days, dict(totals), [dict(r) for r in by_model],
                          [dict(r) for r in by_key], [dict(r) for r in daily])
```
- [ ] **Step 6: Full suite green** (`pytest -q`). Commit `git add ui/app/routes/usage_routes.py ui/tests/test_usage_summary.py && git commit -m "feat(ui): /api/usage/summary — SQL spend-by-model/key + daily over a range"`

### Part B — Usage.svelte rework
**Files:** Modify `ui/frontend/src/routes/Usage.svelte`. **READ it first** (preserve any working bits; it currently calls `/api/usage`).

- [ ] **Step 7:** Add `let days = $state(30)` and load from the new endpoint: `const d = await api.get(`/api/usage/summary?days=${days}`)` (use the existing `api` helper; add a method if needed). Re-fetch when `days` changes (`$effect` or an onchange).
- [ ] **Step 8:** Render: a **range selector** (7 / 30 / 90 buttons bound to `days`); a **totals header** (Spend `$d.totals.spend`, Requests, Tokens); a **Spend by model** table (model / spend / requests / tokens) from `d.by_model`; a **Spend by key** table (key / spend / requests / last used) from `d.by_key`; a **Daily** row of CSS bars from `d.daily` (bar height ∝ requests, title = `${day}: ${requests} req, $${spend}`). Reuse existing table/card classes. Handle empty arrays with a "No usage in this range" line.
- [ ] **Step 9:** Build → succeeds. Commit `git add ui/frontend/src/routes/Usage.svelte ui/frontend/src/lib/api.js && git commit -m "feat(ui): richer Usage — range selector, totals, by-model/by-key tables, daily bars"`

---

## Task 3: Live logs + Debug toggle

### Part A — backend SSE + de-framer (TDD the de-framer)
**Files:** Create `ui/app/routes/logs_routes.py` + `ui/tests/test_logs_deframe.py`. Modify `ui/app/main.py` (register router).

- [ ] **Step 1: Failing test** — `ui/tests/test_logs_deframe.py`:
```python
from app.routes.logs_routes import deframe_docker_log

def _frame(stream, text):
    b = text.encode()
    return bytes([stream, 0, 0, 0]) + len(b).to_bytes(4, "big") + b

def test_deframe_two_complete_frames():
    buf = _frame(1, "line a\n") + _frame(2, "line b\n")
    out, rem = deframe_docker_log(buf)
    assert out == ["line a\n", "line b\n"] and rem == b""

def test_deframe_keeps_incomplete_tail():
    full = _frame(1, "hello\n")
    out, rem = deframe_docker_log(full[:5])           # partial header
    assert out == [] and rem == full[:5]
    out2, rem2 = deframe_docker_log(rem + full[5:])   # completes it
    assert out2 == ["hello\n"] and rem2 == b""

def test_deframe_partial_payload():
    full = _frame(1, "abcdef")
    out, rem = deframe_docker_log(full[:10])           # header + 2 of 6 payload bytes
    assert out == [] and rem == full[:10]
```
- [ ] **Step 2: Run → FAIL.** `cd ui && .venv/bin/python -m pytest tests/test_logs_deframe.py -v`.
- [ ] **Step 3: Create `logs_routes.py`** with the pure de-framer + the SSE endpoint:
```python
import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.auth import login_required
from app.settings import get_settings

router = APIRouter(prefix="/api")


def deframe_docker_log(buf: bytes):
    """Parse complete Docker multiplexed-log frames from buf.
    Each frame: 8-byte header [stream_type, 0,0,0, size(4B big-endian)] + size payload bytes.
    Returns (payloads:list[str], remaining:bytes) — remaining holds an incomplete trailing frame."""
    out, i, n = [], 0, len(buf)
    while n - i >= 8:
        size = int.from_bytes(buf[i + 4:i + 8], "big")
        if n - i - 8 < size:
            break
        out.append(buf[i + 8:i + 8 + size].decode("utf-8", "replace"))
        i += 8 + size
    return out, buf[i:]


async def _log_events(tail: int):
    s = get_settings()
    url = f"{s.socket_proxy_url.rstrip('/')}/containers/{s.litellm_container}/logs"
    params = {"follow": "1", "stdout": "1", "stderr": "1", "timestamps": "1", "tail": str(tail)}
    buf = b""
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("GET", url, params=params) as r:
                if r.status_code >= 400:
                    yield f"data: [log stream unavailable: HTTP {r.status_code}]\n\n"
                    return
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    lines, buf = deframe_docker_log(buf)
                    for ln in lines:
                        for one in ln.rstrip("\n").split("\n"):
                            yield f"data: {one}\n\n"
    except (httpx.HTTPError, Exception) as e:   # client disconnect / upstream drop
        yield f"data: [log stream closed: {type(e).__name__}]\n\n"


@router.get("/logs/stream", dependencies=[Depends(login_required)])
async def logs_stream(tail: int = 200):
    tail = max(1, min(int(tail), 1000))
    return StreamingResponse(_log_events(tail), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```
- [ ] **Step 4: Run → PASS** (de-framer). Register the router in `ui/app/main.py` (mirror the existing `app.include_router(usage_routes.router)` pattern): `from app.routes import logs_routes` + `app.include_router(logs_routes.router)`.
- [ ] **Step 5: Full suite green.** Commit `git add ui/app/routes/logs_routes.py ui/tests/test_logs_deframe.py ui/app/main.py && git commit -m "feat(ui): SSE /api/logs/stream — de-framed live LiteLLM logs via socket-proxy"`

### Part B — Logs.svelte + Debug toggle + sidebar
**Files:** Create `ui/frontend/src/routes/Logs.svelte`. Modify the sidebar/router (where the other screens are registered — grep `Usage` in `ui/frontend/src/App.svelte`).

- [ ] **Step 6:** Create `Logs.svelte`: an `EventSource('/api/logs/stream?tail=200')`, appending `e.data` to a `lines` array (cap at 2000, drop oldest), an auto-scrolling `<pre>`. Controls: **Pause** (`es.close()` / reopen), **Clear** (`lines = []`). Close the EventSource in `onDestroy`. A header note: "Admin-only. Debug level can include request content."
- [ ] **Step 7:** Add the **Debug logging** toggle: read current `set_verbose` from the config store's effective `litellm_setting` items (off if absent). On flip, confirm (`"Raising the log level restarts LiteLLM (~20s) and drops in-flight requests. Continue?"`); if confirmed, `await store.stageItem('litellm_setting', 'set_verbose', checked)` then `await store.apply()` (use the store's existing stage+apply methods — mirror Caching.svelte). Reflect applying state (disable the toggle while applying).
- [ ] **Step 8:** Register **Logs** in the sidebar under the "System" group (next to Housekeeping/Settings) and wire its route, mirroring how `Usage`/`Caching` are registered.
- [ ] **Step 9:** Build → succeeds; backend suite green. Commit `git add ui/frontend/src/routes/Logs.svelte ui/frontend/src/App.svelte && git commit -m "feat(ui): Logs screen — live follow (SSE) + Debug-logging toggle (set_verbose)"`

---

## Task 4: Integration verification + release

- [ ] **Step 1:** Local-build stack (`docker-compose.override.yml` → `build: ./ui`); seed config; `docker compose up -d --build --wait`; catalog sync; Playwright on **`http://10.0.20.85:8081`** (LAN-IP, hard-reload).
- [ ] **Step 2 — health dots (#1):** add a model with a valid credential (or reuse one), Apply, wait for a background health cycle (or hit the model once), reload Models → its dot is **green** with tooltip "Healthy" (was grey). Screenshot.
- [ ] **Step 3 — Usage (#2):** issue 2-3 chat completions through the proxy (master key) to seed `LiteLLM_SpendLogs`; open Usage → totals non-zero, by-model + by-key tables populated, daily bar present; switch range 7/30/90 → re-queries. Screenshot.
- [ ] **Step 4 — logs + Debug (#3):** open Logs → lines stream live (issue a request → see it appear); **verify the socket-proxy serves `/logs`** — if the stream shows "HTTP 403", add the allow-env to the `socket-proxy` service (`docker-compose.yml` + `.env.example`) and re-test. Flip **Debug logging** on → confirm → LiteLLM restarts → after it's back, the stream shows verbose/router lines; confirm `config.yaml` preview has `litellm_settings: {set_verbose: true}`. Flip off → restarts back. Screenshot.
- [ ] **Step 5:** Full backend suite green; screenshots into `docs/images/` (`v37-health.png`, `v37-usage.png`, `v37-logs.png`); teardown; restore `config/config.yaml` from example; remove override; `git status` clean.
- [ ] **Step 6 — release:** merge `v3.7-observability` → `main` (`--no-ff`), push → CI cuts **`1.17.0`** + image; bump compose/admin-ui pin to `1.17.0` (rebase past the release commit); push. If a socket-proxy allow-env was added, note it in the host-update message.

## Self-Review
- **Spec coverage:** #1 health fix → T1; #2 richer Usage → T2 (SQL endpoint + shaper + screen); #3 live logs + Debug → T3 (SSE + de-framer + Logs screen + set_verbose); verify+release → T4. ✓
- **Type consistency:** `_shape_summary(days, totals, by_model, by_key, daily)` (T2) returns the spec's documented dict; `deframe_docker_log(buf) -> (list[str], bytes)` (T3) used by `_log_events`; health map keyed by `model_id`/`item.name` (T1); `set_verbose` staged as a `litellm_setting` item (T3) consistent with the v3 model. ✓
- **Placeholders:** backend helpers + SQL + tests are complete; frontend steps give exact state vars, EventSource wiring, and the stage+apply calls. ✓
- **Verify-point surfaced:** socket-proxy `/logs` permission is an explicit integration step (T4 Step 4), not an assumption.
