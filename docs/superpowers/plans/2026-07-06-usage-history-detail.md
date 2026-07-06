# Usage Activity History + Transaction Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Recent|History switcher to the Usage activity feed (History scoped to the 24h/7d/30d/90d selector with filters, percentile strip, keyset "Load more") and an expand-in-place per-transaction detail view (spend, cost/1M, TTFT/generation timing, failure error detail).

**Architecture:** Two read-only SQL endpoints in `usage_routes.py` (`/api/usage/activity` replaces `/api/usage/recent`; new `/api/usage/tx/{request_id}`), all query-shaping in pure, unit-tested functions. A new `ActivityFeed.svelte` component owns the feed; `Usage.svelte` hosts it and passes `days`, breakdown labels, and a `refreshTick` signal. Shared formatters move to `lib/format.js`.

**Tech Stack:** FastAPI + asyncpg (backend, `ui/.venv/bin/python -m pytest`); Svelte 5 runes + Vite (`cd ui/frontend && npm run build`); Playwright for integration. NEVER use system `python3` for tests — it lacks fastapi; always `ui/.venv/bin/python`.

## Global Constraints

- **Prompts/responses are never queried or returned** — `messages`, `response`, `proxy_server_request`, and raw `metadata` stay out of API responses (allowlist shaping only; `metadata` is read server-side solely to extract `error_information`).
- All SQL is **parameterized** ($n placeholders) — filter values (model, key, cursor parts) must never be string-interpolated into SQL.
- All timestamps serialized via the existing `_iso_utc` (browser-local rendering rule from 1.23.0).
- DB errors follow the loud-log + `{"error":"query_failed"}` pattern (1.17.1 lesson) — never silently return plausible emptiness; the UI distinguishes "couldn't load" from "no activity".
- Keyset pagination: `ORDER BY l."startTime" DESC, l.request_id DESC` with cursor condition `(l."startTime", l.request_id) < ($ts, $id)`; cursor is opaque `"<iso>|<request_id>"`; malformed cursor → 422.
- History percentile strip is computed **server-side sharing the same WHERE** as the row query (`stats=1`, requested by the frontend on first pages only).
- The window (`days`) applies to **both** Recent and History modes.
- Traceback truncated to **4000** chars. `cost_per_1m = spend/total_tokens*1e6`, `null` when no tokens. `ttft_ms`/`gen_ms` `null` when `completionStartTime` is NULL or timestamps are out of order.
- Failure filter: `l.status = 'failure'`; success filter: `l.status IS DISTINCT FROM 'failure'` (old rows may have NULL status).
- UI: switcher/chips reuse the existing `.range-btn`/`.tab-btn` visual language; mode persisted in `localStorage['usage.activityMode']`; auto-refresh silently reloads Recent only, never History.

---

### Task 1: Backend — `/api/usage/activity` + `/api/usage/tx/{id}` (TDD)

**Files:**
- Modify: `ui/app/routes/usage_routes.py` (add helpers + 2 routes; DELETE `usage_recent` + `_shape_recent`)
- Create: `ui/tests/test_usage_activity.py`
- Modify: `ui/tests/test_usage_routes.py` (replace the `_shape_recent` tz test)
- Modify: `ui/tests/test_usage_summary.py` (replace the two `_shape_recent` tests)

**Interfaces:**
- Consumes: existing `_iso_utc`, `_ms`, `_cache_bool`, `get_settings`, `log`.
- Produces (Task 2 relies on): `GET /api/usage/activity?days&status&model&key&cursor&limit&stats` → `{rows:[{id,time,model,provider,key,tok_in,tok_out,spend,latency_ms,status,cache_hit,call_type}], next_cursor, stats?:{count,err_pct,p50_ms,p90_ms,p95_ms,p99_ms}, error?}`; `GET /api/usage/tx/{request_id}` → the spec's tx shape (404 unknown id).

- [ ] **Step 1: Write the failing tests** — create `ui/tests/test_usage_activity.py`:

```python
import json, types, pytest
from datetime import datetime, timezone
import app.routes.usage_routes as ur

NAIVE = datetime(2026, 7, 6, 12, 0, 0)

# ── cursor ──────────────────────────────────────────────────────────────────
def test_cursor_roundtrip():
    s = ur._encode_cursor(NAIVE, "req-1")
    ts, rid = ur._decode_cursor(s)
    assert ts == NAIVE and rid == "req-1"          # decoded back to naive-UTC for the DB

def test_cursor_malformed_raises():
    for bad in ("", "noseparator", "not-a-date|x", "|onlyid"):
        with pytest.raises(ValueError):
            ur._decode_cursor(bad)

# ── WHERE builder (parameterized — values never in SQL text) ────────────────
def test_where_days_only():
    sql, params = ur._activity_where(30)
    assert 'make_interval(days => $1)' in sql and params == [30]

def test_where_all_filters_parameterized():
    sql, params = ur._activity_where(7, status="failure", model="gpt-4o", key="ci",
                                     cursor=(NAIVE, "req-9"))
    assert "l.status = 'failure'" in sql
    assert "l.model = $2" in sql and "COALESCE(v.key_alias, LEFT(l.api_key,10)) = $3" in sql
    assert '(l."startTime", l.request_id) < ($4, $5)' in sql
    assert params == [7, "gpt-4o", "ci", NAIVE, "req-9"]
    assert "gpt-4o" not in sql and "ci" not in sql      # injection safety

def test_where_success_and_none_model():
    sql, _ = ur._activity_where(7, status="success", model="(none)")
    assert "l.status IS DISTINCT FROM 'failure'" in sql
    assert "(l.model IS NULL OR l.model = '')" in sql

# ── row/stats shapers ───────────────────────────────────────────────────────
def _row(**kw):
    base = {"id": "r1", "time": NAIVE, "model": "gpt-4o", "provider": "openai",
            "key": "ci", "tok_in": 10, "tok_out": 5, "spend": 0.002,
            "latency_ms": 900, "status": "success", "cache_hit": None, "call_type": "acompletion"}
    base.update(kw); return base

def test_shape_activity_row():
    r = ur._shape_activity_row(_row())
    assert r["time"].endswith("+00:00") and r["status"] == "success" and r["spend"] == 0.002

def test_shape_activity_row_failure_and_nulls():
    r = ur._shape_activity_row(_row(status="failure", tok_in=None, spend=None, model=None))
    assert r["status"] == "failure" and r["tok_in"] == 0 and r["spend"] == 0.0 and r["model"] == ""

def test_shape_stats():
    s = ur._shape_stats({"n": 12, "err_pct": 8.5, "pcts": [100.0, 200.0, 250.0, 400.4]})
    assert s == {"count": 12, "err_pct": 8.5, "p50_ms": 100, "p90_ms": 200, "p95_ms": 250, "p99_ms": 400}

def test_shape_stats_empty_window():
    s = ur._shape_stats({"n": 0, "err_pct": None, "pcts": None})
    assert s == {"count": 0, "err_pct": 0.0, "p50_ms": None, "p90_ms": None, "p95_ms": None, "p99_ms": None}

# ── error extraction ────────────────────────────────────────────────────────
def test_extract_error_full_and_truncation():
    md = json.dumps({"error_information": {"error_class": "RateLimitError", "error_code": 429,
                     "error_message": "slow down", "llm_provider": "openai", "traceback": "x" * 9000}})
    e = ur._extract_error(md)
    assert e["class"] == "RateLimitError" and e["code"] == "429" and len(e["traceback"]) == 4000

def test_extract_error_absent_or_junk():
    assert ur._extract_error(None) is None
    assert ur._extract_error("not-json{") is None
    assert ur._extract_error(json.dumps({"other": 1})) is None

# ── tx shaping ──────────────────────────────────────────────────────────────
def _txrow(**kw):
    base = {"id": "r1", "time": NAIVE, "end_time": datetime(2026, 7, 6, 12, 0, 9),
            "completion_start": datetime(2026, 7, 6, 12, 0, 2), "call_type": "acompletion",
            "status": "success", "cache_hit": "False", "model_group": "gpt-4o", "model": "gpt-4o",
            "model_id": "mid", "provider": "openai", "api_base": "https://x", "key": "ci",
            "team_id": None, "end_user": None, "session_id": "s1", "tags": json.dumps(["a"]),
            "tok_in": 200, "tok_out": 100, "tok_total": 300, "spend": 0.003,
            "latency_ms": 9000, "metadata": "{}"}
    base.update(kw); return base

def test_shape_tx_derivations():
    t = ur._shape_tx(_txrow())
    assert t["cost_per_1m"] == pytest.approx(10.0)     # 0.003/300*1e6
    assert t["ttft_ms"] == 2000 and t["gen_ms"] == 7000
    assert t["tags"] == ["a"] and t["error"] is None
    assert t["time"].endswith("+00:00")

def test_shape_tx_null_and_inverted_times():
    t = ur._shape_tx(_txrow(completion_start=None, tok_total=0, spend=0.0))
    assert t["ttft_ms"] is None and t["gen_ms"] is None and t["cost_per_1m"] is None
    t2 = ur._shape_tx(_txrow(completion_start=datetime(2026, 7, 6, 13, 0, 0)))  # after end_time
    assert t2["gen_ms"] is None

def test_shape_tx_failure_error():
    md = json.dumps({"error_information": {"error_class": "Exception", "error_message": "boom"}})
    t = ur._shape_tx(_txrow(status="failure", metadata=md))
    assert t["error"]["message"] == "boom" and t["status"] == "failure"

# ── routes (direct-call style, like test_usage_summary_binds_days_as_int) ───
class FakeConn:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []; self.row = row; self.queries = []
    async def fetch(self, q, *a): self.queries.append((q, a)); return self.rows
    async def fetchrow(self, q, *a): self.queries.append((q, a)); return self.row
    async def close(self): pass

def _patch(monkeypatch, conn):
    async def fake_connect(dsn): return conn
    monkeypatch.setattr(ur.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(ur, "get_settings", lambda: types.SimpleNamespace(database_url="postgres://x/y"))

class _Rec(dict):        # asyncpg Record stand-in: mapping + key access
    pass

@pytest.mark.asyncio
async def test_activity_emits_cursor_when_page_full(monkeypatch):
    rows = [_Rec(_row(id=f"r{i}", time=NAIVE)) for i in range(2)]
    _patch(monkeypatch, FakeConn(rows=rows))
    out = await ur.usage_activity(days=7, limit=2)
    assert len(out["rows"]) == 2 and out["next_cursor"] is not None and "stats" not in out

@pytest.mark.asyncio
async def test_activity_no_cursor_on_short_page_and_stats(monkeypatch):
    conn = FakeConn(rows=[_Rec(_row())])
    conn.row = _Rec({"n": 1, "err_pct": 0.0, "pcts": [1.0, 2.0, 3.0, 4.0]})
    _patch(monkeypatch, conn)
    out = await ur.usage_activity(days=7, limit=50, stats=1)
    assert out["next_cursor"] is None and out["stats"]["count"] == 1

@pytest.mark.asyncio
async def test_activity_malformed_cursor_422(monkeypatch):
    from fastapi import HTTPException
    _patch(monkeypatch, FakeConn())
    with pytest.raises(HTTPException) as e:
        await ur.usage_activity(days=7, cursor="garbage")
    assert e.value.status_code == 422

@pytest.mark.asyncio
async def test_tx_404(monkeypatch):
    from fastapi import HTTPException
    _patch(monkeypatch, FakeConn(row=None))
    with pytest.raises(HTTPException) as e:
        await ur.usage_tx("nope")
    assert e.value.status_code == 404

@pytest.mark.asyncio
async def test_activity_empty_dsn_guard(monkeypatch):
    monkeypatch.setattr(ur, "get_settings", lambda: types.SimpleNamespace(database_url=""))
    out = await ur.usage_activity(days=7)
    assert out == {"rows": [], "next_cursor": None}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && .venv/bin/python -m pytest tests/test_usage_activity.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_encode_cursor'` (etc.)

- [ ] **Step 3: Implement in `ui/app/routes/usage_routes.py`**

3a. Change the imports at the top:
```python
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
```

3b. **Delete** `_shape_recent` (near line 92) and the whole `usage_recent` route (near line 187). Add in their place (helpers next to `_cache_bool`, routes at the end of the file):

```python
# ── activity feed: cursor + WHERE + shapers ─────────────────────────────────

def _encode_cursor(ts, rid):
    return f"{_iso_utc(ts)}|{rid}"


def _decode_cursor(s):
    """Opaque keyset cursor '<iso>|<request_id>' → (naive-UTC datetime, id).
    Raises ValueError on anything malformed (route maps it to 422)."""
    ts_s, sep, rid = (s or "").partition("|")
    if not sep or not rid:
        raise ValueError("malformed cursor")
    try:
        ts = datetime.fromisoformat(ts_s)
    except Exception as e:
        raise ValueError("malformed cursor") from e
    if ts.tzinfo is not None:                       # DB column is naive UTC
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts, rid


def _activity_where(days, status="all", model=None, key=None, cursor=None):
    """Build the parameterized WHERE for the activity queries.
    Returns (sql, params); values only ever appear in params ($n placeholders)."""
    clauses = ['l."startTime" > now() - make_interval(days => $1)']
    params = [days]
    if status == "failure":
        clauses.append("l.status = 'failure'")
    elif status == "success":
        clauses.append("l.status IS DISTINCT FROM 'failure'")
    if model:
        if model == "(none)":                        # the by_model placeholder label
            clauses.append("(l.model IS NULL OR l.model = '')")
        else:
            params.append(model)
            clauses.append(f"l.model = ${len(params)}")
    if key:
        params.append(key)
        clauses.append(f"COALESCE(v.key_alias, LEFT(l.api_key,10)) = ${len(params)}")
    if cursor:
        ts, rid = cursor
        params.append(ts); n_ts = len(params)
        params.append(rid); n_id = len(params)
        clauses.append(f'(l."startTime", l.request_id) < (${n_ts}, ${n_id})')
    return " AND ".join(clauses), params


def _shape_activity_row(r):
    return {"id": r["id"], "time": _iso_utc(r["time"]), "model": r["model"] or "",
            "provider": r["provider"] or "", "key": r["key"],
            "tok_in": r["tok_in"] or 0, "tok_out": r["tok_out"] or 0,
            "spend": float(r["spend"] or 0), "latency_ms": r["latency_ms"] or 0,
            "status": "failure" if r["status"] == "failure" else "success",
            "cache_hit": _cache_bool(r["cache_hit"]), "call_type": r.get("call_type") or ""}


def _shape_stats(r):
    pcts = r.get("pcts") or [None, None, None, None]
    return {"count": r.get("n") or 0, "err_pct": float(r.get("err_pct") or 0),
            "p50_ms": _ms(pcts[0]), "p90_ms": _ms(pcts[1]),
            "p95_ms": _ms(pcts[2]), "p99_ms": _ms(pcts[3])}


def _extract_error(metadata, max_tb=4000):
    """Pull metadata.error_information (class/code/message/provider/traceback).
    Defensive: jsonb arrives as str from asyncpg; any malformed shape → None."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            return None
    if not isinstance(metadata, dict):
        return None
    ei = metadata.get("error_information")
    if not isinstance(ei, dict):
        return None
    return {"class": ei.get("error_class") or "", "code": str(ei.get("error_code") or ""),
            "message": ei.get("error_message") or "", "provider": ei.get("llm_provider") or "",
            "traceback": (ei.get("traceback") or "")[:max_tb]}


def _shape_tx(r):
    def ms_between(a, b):
        if a is None or b is None:
            return None
        d = (b - a).total_seconds() * 1000
        return int(round(d)) if d >= 0 else None
    tags = r.get("tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []
    tok_total = r["tok_total"] or 0
    spend = float(r["spend"] or 0)
    st, cst, et = r["time"], r.get("completion_start"), r.get("end_time")
    return {"id": r["id"], "time": _iso_utc(st), "end_time": _iso_utc(et),
            "call_type": r.get("call_type") or "",
            "status": "failure" if r["status"] == "failure" else "success",
            "cache_hit": _cache_bool(r["cache_hit"]),
            "model_group": r.get("model_group") or "", "model": r["model"] or "",
            "model_id": r.get("model_id") or "", "provider": r.get("provider") or "",
            "api_base": r.get("api_base") or "", "key": r["key"],
            "team_id": r.get("team_id") or "", "end_user": r.get("end_user") or "",
            "session_id": r.get("session_id") or "",
            "tags": tags if isinstance(tags, list) else [],
            "tok_in": r["tok_in"] or 0, "tok_out": r["tok_out"] or 0, "tok_total": tok_total,
            "spend": spend, "cost_per_1m": (spend / tok_total * 1e6) if tok_total else None,
            "latency_ms": r["latency_ms"] or 0,
            "ttft_ms": ms_between(st, cst), "gen_ms": ms_between(cst, et),
            "error": _extract_error(r.get("metadata")) if r["status"] == "failure" else None}
```

3c. The two routes (at the end of the file, replacing the deleted `usage_recent`):

```python
_ACTIVITY_SELECT = (
    'SELECT l.request_id id, l."startTime" time, l.model, l.custom_llm_provider provider, '
    'COALESCE(v.key_alias, LEFT(l.api_key,10)) key, l.prompt_tokens tok_in, '
    'l.completion_tokens tok_out, l.spend, l.request_duration_ms latency_ms, '
    'l.status, l.cache_hit, l.call_type '
    'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token=l.api_key ')

_ACTIVITY_STATS = (
    'SELECT COUNT(*) n, '
    "100.0*COUNT(*) FILTER (WHERE l.status='failure')/NULLIF(COUNT(*),0) err_pct, "
    'percentile_cont(ARRAY[0.5,0.9,0.95,0.99]) WITHIN GROUP (ORDER BY l.request_duration_ms) '
    '  FILTER (WHERE l.request_duration_ms>0) pcts '
    'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token=l.api_key ')


@router.get("/usage/activity", dependencies=[Depends(login_required)])
async def usage_activity(days: int = 30, status: str = "all", model: str = "",
                         key: str = "", cursor: str = "", limit: int = 50, stats: int = 0):
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 200))
    if status not in ("all", "success", "failure"):
        raise HTTPException(status_code=422, detail="status must be all|success|failure")
    cur = None
    if cursor:
        try:
            cur = _decode_cursor(cursor)
        except ValueError:
            raise HTTPException(status_code=422, detail="malformed cursor")
    dsn = get_settings().database_url
    if not dsn:
        return {"rows": [], "next_cursor": None}
    where, params = _activity_where(days, status, model or None, key or None, cur)
    conn = await asyncpg.connect(dsn)
    try:
        row_params = params + [limit]
        rows = await conn.fetch(
            f'{_ACTIVITY_SELECT} WHERE {where} '
            f'ORDER BY l."startTime" DESC, l.request_id DESC LIMIT ${len(row_params)}',
            *row_params)
        out = {"rows": [_shape_activity_row(dict(r)) for r in rows],
               "next_cursor": _encode_cursor(rows[-1]["time"], rows[-1]["id"])
                              if len(rows) == limit else None}
        if stats:
            srow = await conn.fetchrow(f'{_ACTIVITY_STATS} WHERE {where}', *params)
            out["stats"] = _shape_stats(dict(srow) if srow else {})
        return out
    except Exception:
        log.exception("usage_activity query failed (days=%s status=%s)", days, status)
        return {"rows": [], "next_cursor": None, "error": "query_failed"}
    finally:
        await conn.close()


@router.get("/usage/tx/{request_id}", dependencies=[Depends(login_required)])
async def usage_tx(request_id: str):
    dsn = get_settings().database_url
    if not dsn:
        raise HTTPException(status_code=404, detail="transaction not found")
    conn = await asyncpg.connect(dsn)
    try:
        r = await conn.fetchrow(
            'SELECT l.request_id id, l."startTime" time, l."endTime" end_time, '
            'l."completionStartTime" completion_start, l.call_type, l.status, l.cache_hit, '
            'l.model_group, l.model, l.model_id, l.custom_llm_provider provider, l.api_base, '
            'COALESCE(v.key_alias, LEFT(l.api_key,10)) key, l.team_id, l.end_user, l.session_id, '
            'l.request_tags tags, l.prompt_tokens tok_in, l.completion_tokens tok_out, '
            'l.total_tokens tok_total, l.spend, l.request_duration_ms latency_ms, l.metadata '
            'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token=l.api_key '
            'WHERE l.request_id = $1 LIMIT 1', request_id)
    except Exception:
        log.exception("usage_tx query failed")
        raise HTTPException(status_code=502, detail="query failed")
    finally:
        await conn.close()
    if r is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return _shape_tx(dict(r))
```

3d. Update the two old test files (they reference the deleted `_shape_recent`):

In `ui/tests/test_usage_routes.py`, replace `test_shape_recent_time_has_utc_offset` with:
```python
def test_shape_activity_row_time_has_utc_offset():
    row = {"id": "r1", "time": datetime(2026, 6, 21, 12, 0, 0), "model": "m", "provider": "p",
           "key": "k", "tok_in": 1, "tok_out": 2, "spend": 0.0, "latency_ms": 3,
           "status": "success", "cache_hit": None, "call_type": "acompletion"}
    assert _ur._shape_activity_row(row)["time"].endswith("+00:00")
```

In `ui/tests/test_usage_summary.py`, delete `from app.routes.usage_routes import _shape_recent` (and its section) and replace `test_shape_recent_maps_rows` / `test_shape_recent_cache_false_and_none` with:
```python
from app.routes.usage_routes import _shape_activity_row

def test_shape_activity_row_maps_fields():
    r = _shape_activity_row({"id": "rid", "time": _dt(2026, 6, 19, 18, 42, 3),
        "model": "deepinfra/openai/gpt-oss-20b", "provider": "deepinfra", "key": "hindsight-cbr",
        "tok_in": 1200, "tok_out": 340, "spend": 0.004, "latency_ms": 41200,
        "status": "success", "cache_hit": "True", "call_type": "acompletion"})
    assert r["time"] == "2026-06-19T18:42:03+00:00" and r["cache_hit"] is True
    assert r["id"] == "rid" and r["latency_ms"] == 41200

def test_shape_activity_row_cache_false_and_none():
    base = {"id": "r", "time": _dt(2026, 6, 19, 1, 0), "model": "m", "provider": "groq",
            "key": "k", "tok_in": 1, "tok_out": 2, "spend": 0.0, "latency_ms": 500,
            "status": "success", "call_type": ""}
    assert _shape_activity_row({**base, "cache_hit": "False"})["cache_hit"] is False
    assert _shape_activity_row({**base, "cache_hit": None})["cache_hit"] is None
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_usage_activity.py tests/test_usage_routes.py tests/test_usage_summary.py -q` → all PASS.
Then the full suite: `cd ui && .venv/bin/python -m pytest tests/ -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add ui/app/routes/usage_routes.py ui/tests/test_usage_activity.py ui/tests/test_usage_routes.py ui/tests/test_usage_summary.py
git commit -m "feat: usage activity endpoint (windowed, filtered, keyset-paged, stats) + per-tx detail endpoint"
```

---

### Task 2: Frontend — `ActivityFeed.svelte` + Usage host + `lib/format.js`

**Files:**
- Create: `ui/frontend/src/lib/format.js`
- Create: `ui/frontend/src/routes/ActivityFeed.svelte`
- Modify: `ui/frontend/src/routes/Usage.svelte` (remove inline feed; host the component)

**Interfaces:**
- Consumes: Task 1's `/api/usage/activity` + `/api/usage/tx/{id}` shapes; `copyText` from `lib/browser.js`; `api` from `lib/api.js` (uses `api.get`, already generic — no api.js change needed).
- Produces: `<ActivityFeed days={days} byModel={[...]} byKey={[...]} refreshTick={n} />`.

- [ ] **Step 1: Create `ui/frontend/src/lib/format.js`** (shared formatters, moved out of Usage.svelte):

```js
export const money = (n) => `$${Number(n ?? 0).toFixed(4)}`
export function fmtMs(ms) {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}
```

- [ ] **Step 2: Create `ui/frontend/src/routes/ActivityFeed.svelte`**:

```svelte
<script>
  import { api } from '../lib/api.js'
  import { copyText } from '../lib/browser.js'
  import { money, fmtMs } from '../lib/format.js'

  let { days, byModel = [], byKey = [], refreshTick = 0 } = $props()

  function initMode() { return localStorage.getItem('usage.activityMode') === 'history' ? 'history' : 'recent' }
  let mode = $state(initMode())
  $effect(() => localStorage.setItem('usage.activityMode', mode))

  // History filters (session-scoped, not persisted)
  let fStatus = $state('all')
  let fModel = $state('')
  let fKey = $state('')

  let rows = $state([])
  let stats = $state(null)
  let nextCursor = $state(null)
  let busy = $state(false)
  let feedErr = $state('')

  let openId = $state(null)
  let detail = $state({})   // id → { loading?|data?|error? }

  const qs = (p) => Object.entries(p)
    .filter(([, v]) => v !== '' && v != null && v !== 0 || v === 0 && false)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')

  async function loadFirst(reset = true) {
    busy = true; feedErr = ''
    try {
      const p = { days, limit: 50 }
      if (mode === 'history') Object.assign(p, { status: fStatus, model: fModel, key: fKey, stats: 1 })
      const d = await api.get(`/api/usage/activity?${qs(p)}`)
      if (d.error) { feedErr = "Couldn't load activity — the query failed (check the UI logs). This is not the same as no activity."; rows = []; stats = null; nextCursor = null; return }
      rows = d.rows ?? []
      nextCursor = mode === 'history' ? (d.next_cursor ?? null) : null
      stats = d.stats ?? null
      if (reset) { openId = null; detail = {} }
      else if (openId && !rows.some(r => r.id === openId)) openId = null
    } catch (e) { feedErr = e.message; rows = [] }
    finally { busy = false }
  }

  async function loadMore() {
    if (!nextCursor || busy) return
    busy = true; feedErr = ''
    try {
      const p = { days, limit: 50, status: fStatus, model: fModel, key: fKey, cursor: nextCursor }
      const d = await api.get(`/api/usage/activity?${qs(p)}`)
      if (d.error) { feedErr = "Couldn't load more — the query failed."; return }
      rows = [...rows, ...(d.rows ?? [])]
      nextCursor = d.next_cursor ?? null
    } catch (e) { feedErr = e.message }
    finally { busy = false }
  }

  // mode / window / filter change → full reload (collapses detail)
  $effect(() => { mode; days; fStatus; fModel; fKey; loadFirst(true) })
  // auto-refresh signal from the host: silently refresh Recent only (History never rug-pulls)
  let _prevTick = 0
  $effect(() => {
    const t = refreshTick
    if (t !== _prevTick) { _prevTick = t; if (mode === 'recent') loadFirst(false) }
  })

  async function toggle(id) {
    if (openId === id) { openId = null; return }
    openId = id
    if (!detail[id]) {
      detail = { ...detail, [id]: { loading: true } }
      try {
        const d = await api.get(`/api/usage/tx/${encodeURIComponent(id)}`)
        detail = { ...detail, [id]: { data: d } }
      } catch (e) {
        detail = { ...detail, [id]: { error: e.message } }
      }
    }
  }
  function retry(id) { detail = { ...detail, [id]: undefined }; openId = null; toggle(id) }

  function timing(d) {
    const parts = []
    if (d.ttft_ms != null) parts.push(`TTFT ${fmtMs(d.ttft_ms)}`)
    if (d.gen_ms != null) parts.push(`generation ${fmtMs(d.gen_ms)}`)
    parts.push(`total ${fmtMs(d.latency_ms)}`)
    return parts.join(' · ')
  }
</script>

<div class="card">
  <div class="feed-head">
    <h2>Activity</h2>
    <div class="seg">
      <button class="seg-btn" class:active={mode === 'recent'} onclick={() => mode = 'recent'}>Recent</button>
      <button class="seg-btn" class:active={mode === 'history'} onclick={() => mode = 'history'}>History</button>
    </div>
  </div>

  {#if mode === 'history'}
    <div class="chips">
      <div class="seg small">
        {#each [['all','All'],['success','Success'],['failure','Failure']] as [v, label]}
          <button class="seg-btn" class:active={fStatus === v} onclick={() => fStatus = v}>{label}</button>
        {/each}
      </div>
      <select bind:value={fModel} aria-label="filter model">
        <option value="">All models</option>
        {#each byModel as m}<option value={m}>{m}</option>{/each}
      </select>
      <select bind:value={fKey} aria-label="filter key">
        <option value="">All keys</option>
        {#each byKey as k}<option value={k}>{k}</option>{/each}
      </select>
    </div>
    {#if stats}
      <div class="strip">
        <span class="pill">{stats.count.toLocaleString()} requests</span>
        <span class="pill" class:red={stats.err_pct > 0}>err {stats.err_pct.toFixed(1)}%</span>
        <span class="pill">p50 {fmtMs(stats.p50_ms)}</span>
        <span class="pill">p90 {fmtMs(stats.p90_ms)}</span>
        <span class="pill">p95 {fmtMs(stats.p95_ms)}</span>
        <span class="pill">p99 {fmtMs(stats.p99_ms)}</span>
      </div>
    {/if}
  {/if}

  {#if feedErr}<div class="banner err">{feedErr}</div>{/if}

  {#if rows.length === 0 && !busy && !feedErr}
    <p class="empty">No activity in this range.</p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Model</th><th>Provider</th><th>Key</th>
            <th>Tok in</th><th>Tok out</th><th>Spend</th><th>Latency</th><th>Status</th><th>Cache</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r (r.id)}
            <tr class="row" class:failed={r.status === 'failure'} class:open={openId === r.id}
                onclick={() => toggle(r.id)}>
              <td class="nowrap">{new Date(r.time).toLocaleTimeString()}</td>
              <td class="trunc" title={r.model}>{r.model || '—'}</td>
              <td>{r.provider || '—'}</td>
              <td>{r.key}</td>
              <td>{(r.tok_in ?? 0).toLocaleString()}</td>
              <td>{(r.tok_out ?? 0).toLocaleString()}</td>
              <td>{money(r.spend)}</td>
              <td>{fmtMs(r.latency_ms)}</td>
              <td class:green={r.status === 'success'} class:red={r.status !== 'success'}>
                {r.status === 'success' ? '✓' : '✗ failed'}
              </td>
              <td>{r.cache_hit === true ? 'hit' : r.cache_hit === false ? 'miss' : '—'}</td>
            </tr>
            {#if openId === r.id}
              {@const d = detail[r.id]}
              <tr class="detail-row"><td colspan="10">
                {#if d?.loading}<p class="empty">Loading detail…</p>
                {:else if d?.error}<p class="empty">Couldn't load detail — {d.error} <button class="linkbtn" onclick={() => retry(r.id)}>retry</button></p>
                {:else if d?.data}
                  {@const t = d.data}
                  <div class="dgrid">
                    <span class="dl">Request</span>
                    <span class="dv mono">{t.id} <button class="linkbtn" onclick={(e) => { e.stopPropagation(); copyText(t.id) }}>Copy</button></span>
                    <span class="dl">Call</span><span class="dv">{t.call_type || '—'}</span>
                    <span class="dl">Route</span>
                    <span class="dv">{t.model_group || '—'} → {t.model || '—'}{t.provider ? ` (${t.provider})` : ''}</span>
                    {#if t.api_base}<span class="dl">API base</span><span class="dv mono">{t.api_base}</span>{/if}
                    <span class="dl">Tokens</span><span class="dv">{t.tok_in.toLocaleString()} in / {t.tok_out.toLocaleString()} out / {t.tok_total.toLocaleString()} total</span>
                    <span class="dl">Spend</span>
                    <span class="dv">{money(t.spend)}{t.cost_per_1m != null ? ` · $${t.cost_per_1m.toFixed(4)}/1M` : ''}</span>
                    <span class="dl">Timing</span><span class="dv">{timing(t)}</span>
                    <span class="dl">Cache</span><span class="dv">{t.cache_hit === true ? 'hit' : t.cache_hit === false ? 'miss' : '—'}</span>
                    {#if t.session_id}<span class="dl">Session</span><span class="dv mono">{t.session_id}</span>{/if}
                    {#if t.end_user}<span class="dl">End user</span><span class="dv">{t.end_user}</span>{/if}
                    {#if t.tags.length}<span class="dl">Tags</span><span class="dv">{t.tags.join(', ')}</span>{/if}
                  </div>
                  {#if t.error}
                    <div class="errbox">
                      <div class="errhead">{t.error.class || 'Error'}{t.error.code ? ` (${t.error.code})` : ''}{t.error.provider ? ` — ${t.error.provider}` : ''}</div>
                      <div class="errmsg">{t.error.message}</div>
                      {#if t.error.traceback}
                        <details><summary>Traceback</summary><pre>{t.error.traceback}</pre></details>
                      {/if}
                    </div>
                  {/if}
                {/if}
              </td></tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
    {#if mode === 'history' && nextCursor}
      <div class="more"><button class="fb-add" onclick={loadMore} disabled={busy}>{busy ? 'Loading…' : 'Load more'}</button></div>
    {/if}
  {/if}
</div>

<style>
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  h2{font-size:15px;margin:0}
  .feed-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
  .seg{display:flex;gap:6px}
  .seg-btn{padding:4px 14px;border:1px solid rgba(0,0,0,.15);border-radius:20px;background:#f5f5f7;font-size:13px;cursor:pointer;transition:background .15s}
  .seg-btn:hover{background:#e5e5ea}
  .seg-btn.active{background:#0a84ff;color:#fff;border-color:#0a84ff}
  .seg.small .seg-btn{padding:3px 10px;font-size:12px}
  .chips{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
  .chips select{font-size:13px;padding:4px 8px;border:1px solid rgba(0,0,0,.15);border-radius:8px;background:#fff}
  .strip{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
  .pill{font-size:12px;padding:3px 10px;border-radius:20px;background:#f5f5f7;color:#3a3a3c}
  .pill.red{background:#ffeceb;color:#c0271d}
  .table-wrap{overflow-x:auto}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:13px;white-space:nowrap}
  tr.row{cursor:pointer}
  tr.row:hover td{background:rgba(0,0,0,.03)}
  tr.row.failed td{background:#fff7f6}
  tr.row.open td{background:rgba(10,132,255,.06)}
  .detail-row td{background:#fafafc;white-space:normal}
  .dgrid{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;padding:6px 2px;font-size:13px}
  .dl{color:#6e6e73}
  .dv{overflow-wrap:anywhere}
  .mono{font-family:"SF Mono","Fira Code",monospace;font-size:12px}
  .errbox{margin:8px 2px 4px;padding:10px 12px;background:#ffeceb;border-radius:8px;font-size:13px}
  .errhead{font-weight:600;color:#c0271d}
  .errmsg{margin-top:4px;color:#3a3a3c;overflow-wrap:anywhere}
  .errbox pre{margin:6px 0 0;max-height:240px;overflow:auto;font-size:11px;white-space:pre-wrap}
  .errbox summary{cursor:pointer;font-size:12px;color:#6e6e73;margin-top:6px}
  .more{margin-top:10px;text-align:center}
  .fb-add{font-size:12px;padding:4px 12px;border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer}
  .linkbtn{background:none;border:0;padding:0;color:#0a84ff;cursor:pointer;font:inherit;font-size:12px;text-decoration:underline}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin:6px 0;font-size:13px}
  .empty{color:#6e6e73}
  .red{color:#c0271d}
  .green{color:#1a7f37}
  .trunc{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .nowrap{white-space:nowrap}
</style>
```

Note: the `qs` helper above has a subtle boolean expression — simplify it during implementation to exactly:
```js
const qs = (p) => Object.entries(p)
  .filter(([, v]) => v !== '' && v != null)
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
```
(`stats: 1`, `days`, `limit` are numbers and pass the filter; empty-string filters are dropped, matching the backend's `model or None` handling.)

- [ ] **Step 3: Modify `ui/frontend/src/routes/Usage.svelte`**

3a. Imports: add `import ActivityFeed from './ActivityFeed.svelte'` and replace the local formatter definitions with `import { money, fmtMs } from '../lib/format.js'` (delete the `const money = ...` and `function fmtMs(...)` block, lines 43-47).

3b. Feed state/fetch removal: delete `let recent = $state([])` (line 14); change `load()` to fetch only the summary and bump a tick on silent refreshes:
```js
  let refreshTick = $state(0)

  async function load(silent = false) {
    if (!silent) { loading = true; summary = null }
    err = ''
    try {
      summary = await api.get(`/api/usage/summary?days=${days}`)
      if (silent) refreshTick++          // nudge ActivityFeed (Recent mode only)
    } catch (e) { err = e.message }
    finally { if (!silent) loading = false }
  }
```

3c. Replace the whole "Recent activity feed" card (lines 221-255, the `<div class="card"><h2>Recent activity</h2>...` block) with:
```svelte
    <!-- ── Activity feed (Recent | History) ── -->
    <ActivityFeed {days} {refreshTick}
      byModel={(summary.by_model ?? []).map(r => r.label).filter(l => l && l !== '(none)')}
      byKey={(summary.by_key ?? []).map(r => r.label).filter(Boolean)} />
```

- [ ] **Step 4: Build**

Run: `cd ui/frontend && npm run build` → `✓ built`, no errors.

- [ ] **Step 5: Commit**

```bash
git add ui/frontend/src/lib/format.js ui/frontend/src/routes/ActivityFeed.svelte ui/frontend/src/routes/Usage.svelte
git commit -m "feat(ui): Recent|History activity feed with filters, percentile strip, load-more + expand-in-place tx detail"
```

---

### Task 3: Docs

**Files:**
- Modify: `docs/admin-ui-guide.md` (Usage & Spend section — the activity-feed subsection)

- [ ] **Step 1:** In `docs/admin-ui-guide.md`, find the Usage & Spend section's activity/recent-feed subsection (search for a heading containing `Recent activity` or the text `recent activity feed` within the `## Usage & Spend` section) and replace that subsection with:

```markdown
### Activity (Recent | History)

The activity card has a two-mode switcher (persisted per browser):

- **Recent** — the newest requests in the selected range, silently refreshed by
  Auto-refresh. A live tail for "what's happening right now".
- **History** — browse the full selected range (24h/7d/30d/90d — the same range
  selector at the top governs both modes). Filter chips narrow by **Status**
  (All / Success / Failure), **Model**, and **Key**; a stat strip above the list
  shows request count, error %, and **p50/p90/p95/p99 latency computed over
  exactly the filtered set**. **Load more** appends older pages (cursor-based, so
  new incoming traffic never shifts or duplicates what you've already loaded).
  Auto-refresh deliberately leaves History alone — no scroll rug-pulls.

**Click any row** (either mode) to expand its transaction detail in place:
request id (with Copy), call type, route (model group → actual model, provider,
API base), token counts, spend and **cost per 1M tokens**, cache hit, a timing
line (**TTFT · generation · total**, when the backend reported them), session /
end-user / tags — and for failures, the **error class, code, provider, message,
and a collapsible traceback** as recorded by LiteLLM.

> Prompts and responses are **not** stored (LiteLLM's
> `store_prompts_in_spend_logs` is off in this stack) — the detail view is
> metadata-only by design.
```

- [ ] **Step 2: Commit**

```bash
git add docs/admin-ui-guide.md
git commit -m "docs: Usage activity Recent|History + transaction detail"
```

---

### Task 4: Integration sweep, release, deploy (controller)

**Files:** none (verification + release).

- [ ] **Step 1: Full backend suite + frontend build** — `cd ui && .venv/bin/python -m pytest tests/ -q` (expect all pass; count grows from 218) and `cd ui/frontend && npm run build`.

- [ ] **Step 2: Local hybrid stack** — recreate `docker-compose.override.yml` (litellm+UI `STORE_MODEL_IN_DB: "true"`, UI `build: ./ui`), `docker compose up -d --build llm-proxy-ui`, wait healthy.

- [ ] **Step 3: Seed synthetic SpendLogs** (56 success + 1 failure, spread over recent hours):

```bash
docker exec litellm-postgres psql -U "$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)" -d litellm <<'SQL'
INSERT INTO "LiteLLM_SpendLogs"
 (request_id, call_type, api_key, spend, total_tokens, prompt_tokens, completion_tokens,
  "startTime","endTime","completionStartTime", model, model_group, custom_llm_provider,
  metadata, cache_hit, status, request_duration_ms, session_id)
SELECT 'seed-'||g, 'acompletion', 'sk-seed', 0.001*g, 300, 200, 100,
  now() - (g||' minutes')::interval,
  now() - (g||' minutes')::interval + interval '8 seconds',
  now() - (g||' minutes')::interval + interval '2 seconds',
  CASE WHEN g%2=0 THEN 'gpt-4o' ELSE 'gpt-4o-mini' END,
  CASE WHEN g%2=0 THEN 'gpt-4o' ELSE 'gpt-4o-mini' END,
  'openai', '{}', CASE WHEN g%3=0 THEN 'True' ELSE 'False' END, 'success', 8000, 'sess-'||g
FROM generate_series(1, 56) g;
INSERT INTO "LiteLLM_SpendLogs"
 (request_id, call_type, api_key, spend, total_tokens, prompt_tokens, completion_tokens,
  "startTime","endTime", model, model_group, custom_llm_provider, metadata, cache_hit,
  status, request_duration_ms)
VALUES ('seed-fail-1','acompletion','sk-seed',0,0,0,0, now() - interval '5 minutes',
  now() - interval '5 minutes' + interval '1 second', 'gpt-4o','gpt-4o','openai',
  '{"error_information":{"error_class":"RateLimitError","error_code":"429","error_message":"seeded failure for testing","llm_provider":"openai","traceback":"Traceback (most recent call last):\n  seeded"}}',
  NULL,'failure',1000);
SQL
```

- [ ] **Step 4: Playwright checklist** (login at http://10.0.20.85:8081, Usage screen):
  1. Feed shows **Recent | History** switcher; Recent lists newest rows.
  2. Switch to History → stat strip appears (57 seeded + real rows; err% > 0); rows capped at 50; **Load more** visible → click → appends, then hides when exhausted.
  3. Status=Failure chip → only `seed-fail-1` (+ any real failures); strip recomputes (count drops).
  4. Model filter → only that model's rows; strip recomputes.
  5. Click a success row → detail expands: cost/1M present, timing line shows `TTFT 2.0s · generation 6.0s · total 8.0s`, request-id Copy present.
  6. Click the failure row → red-tinted row; detail shows `RateLimitError (429)`, message, expandable traceback.
  7. Switch range 24h↔7d → History reloads within window; switcher choice survives a page reload (localStorage).
  8. Recent mode + auto-refresh 10s → rows update silently, no scroll jump, History untouched.
- [ ] **Step 5: Clean up seeds + teardown**:
```bash
docker exec litellm-postgres psql -U "$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)" -d litellm \
  -c "DELETE FROM \"LiteLLM_SpendLogs\" WHERE request_id LIKE 'seed-%';"
docker compose down && rm docker-compose.override.yml
```
- [ ] **Step 6: Final whole-branch review** (opus, review-package over the branch) → fix Critical/Important → then finishing-a-development-branch: merge `--no-ff` to main (`merge: usage activity history + transaction detail (1.29.0)`), push (CI cuts **1.29.0**), pull bot commit, bump pin to `llm-proxy-ui:1.29.0`, push, verify GHCR manifest, deploy to `.75` UI-only (litellm `StartedAt` unchanged), update memory.

---

## Self-Review

**Spec coverage:** activity endpoint with days/status/model/key/cursor/limit/stats (T1) ✓; tx endpoint, allowlist, 404, traceback cap (T1) ✓; `_iso_utc` everywhere (T1 shapers) ✓; query_failed pattern (both routes) ✓; Recent|History switcher persisted, window on both modes, chips, strip, load-more, no History auto-refresh (T2) ✓; expand-in-place with lazy fetch + cache + retry, cost/1M, timing line, error block (T2) ✓; docs (T3) ✓; unit/route/Playwright matrix incl. seeded failure (T1, T4) ✓; prompts excluded everywhere ✓.

**Placeholder scan:** none — all code steps carry full code; the one flagged simplification (the `qs` filter) includes its exact final form.

**Type consistency:** `_shape_activity_row`/`_shape_stats`/`_shape_tx`/`_encode_cursor`/`_decode_cursor`/`_activity_where` names and shapes match between implementation (T1 Step 3), tests (T1 Step 1), and frontend consumption (T2); `ActivityFeed` props `{days, byModel, byKey, refreshTick}` match the host call in T2 Step 3c.
