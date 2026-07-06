import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
import asyncpg
from app.auth import login_required
from app.spend_client import SpendClient
from app.settings import get_settings

router = APIRouter(prefix="/api")
log = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Shared metric columns used verbatim in all three GROUP BY breakdowns.
# Every query aliases LiteLLM_SpendLogs as `l` so these `l.`-prefixed
# references are unambiguous even in the by_key JOIN where
# LiteLLM_VerificationToken also has a `spend` column.
# ---------------------------------------------------------------------------
_METRIC_COLS = (
    "COUNT(*) requests, SUM(l.prompt_tokens) tok_in, SUM(l.completion_tokens) tok_out, "
    "SUM(l.spend) spend, SUM(l.spend)/NULLIF(SUM(l.total_tokens),0)*1e6 cost_per_1m, "
    "percentile_cont(0.5) WITHIN GROUP (ORDER BY l.request_duration_ms) "
    "  FILTER (WHERE l.request_duration_ms>0) p50_ms, "
    "percentile_cont(0.95) WITHIN GROUP (ORDER BY l.request_duration_ms) "
    "  FILTER (WHERE l.request_duration_ms>0) p95_ms, "
    "100.0*COUNT(*) FILTER (WHERE l.status='failure')/NULLIF(COUNT(*),0) err_pct"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms(v):
    return int(round(v)) if v is not None else None


def _iso_utc(dt):
    """ISO-8601 with an explicit UTC offset.

    LiteLLM_SpendLogs."startTime"/"endTime" are `timestamp WITHOUT time zone`, so
    asyncpg returns NAIVE datetimes whose wall-clock is UTC. A plain `.isoformat()`
    emits no offset, and the browser's `new Date()` then parses it as LOCAL time —
    defeating the frontend's `toLocaleString()` and surfacing raw UTC numbers.
    Stamping +00:00 lets the client convert to the user's local zone.
    """
    if dt is None:
        return None
    # date_trunc()/startTime arrive as naive datetimes whose wall-clock is UTC.
    # A plain date (no time component) carries no zone — emit it unchanged.
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _cols(rows):
    out = []
    for r in rows:
        d = {"label": r["label"], "requests": r["requests"], "tok_in": r["tok_in"] or 0,
             "tok_out": r["tok_out"] or 0, "spend": float(r["spend"] or 0),
             "cost_per_1m": float(r["cost_per_1m"]) if r.get("cost_per_1m") is not None else None,
             "p50_ms": _ms(r.get("p50_ms")), "p95_ms": _ms(r.get("p95_ms")),
             "err_pct": float(r["err_pct"] or 0)}
        if r.get("last_used"):
            d["last_used"] = _iso_utc(r["last_used"])
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
        "timeseries": [{"bucket": _iso_utc(r["bucket"]), "requests": r["requests"],
                        "spend": float(r["spend"] or 0), "p95_ms": _ms(r.get("p95_ms"))} for r in timeseries],
    }


def _cache_bool(v):
    if v in ("True", "true"): return True
    if v in ("False", "false"): return False
    return None


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


# ---------------------------------------------------------------------------
# Legacy SpendClient-based endpoint (v3.x compat)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------

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
            # cache_hit is a string column ('True'/'False'/None). We count rows where
            # cache_hit='True' (or 'true') as hits. Note: most rows may have cache_hit=None
            # (requests where caching was not attempted/checked) — those are excluded from
            # both numerator and denominator, so this rate represents "cache-hit fraction
            # of requests that were cache-eligible" rather than "of all requests".
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
            # Invariant: callers pass stats=1 only on the FIRST page (no cursor), so the
            # strip covers the whole filtered window. If a caller ever sends stats=1 WITH
            # a cursor, `where` carries the cursor clause and the percentiles would be
            # computed over the post-cursor subset — request the strip without a cursor.
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
