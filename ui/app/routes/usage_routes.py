import asyncio
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
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


def _shape_recent(rows):
    return {"recent": [{"time": _iso_utc(r["time"]), "model": r["model"], "provider": r["provider"] or "",
                        "key": r["key"], "tok_in": r["tok_in"] or 0, "tok_out": r["tok_out"] or 0,
                        "latency_ms": r["latency_ms"] or 0, "status": r["status"] or "",
                        "cache_hit": _cache_bool(r["cache_hit"])} for r in rows]}


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
