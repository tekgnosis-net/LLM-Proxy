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


@router.get("/usage/summary", dependencies=[Depends(login_required)])
async def usage_summary(days: int = 30):
    days = max(1, min(int(days), 365))
    dsn = get_settings().database_url
    if not dsn:
        return _shape_summary(days, {"spend": 0, "requests": 0, "tokens": 0}, [], [], [])
    # NOTE: bind the window as an INTEGER day count via make_interval(days => $1).
    # `now() - $1::interval` with a str param makes asyncpg infer $1 as an interval
    # type and reject the str ("'str' object has no attribute 'days'", DataError) —
    # which a silent catch then turned into an all-zeros "no usage" screen.
    conn = await asyncpg.connect(dsn)
    try:
        totals = await conn.fetchrow(
            'SELECT COALESCE(SUM(spend),0) spend, COUNT(*) requests, COALESCE(SUM(total_tokens),0) tokens '
            'FROM "LiteLLM_SpendLogs" WHERE "startTime" > now() - make_interval(days => $1)', days)
        by_model = await conn.fetch(
            'SELECT model, SUM(spend) s, COUNT(*) r, SUM(total_tokens) t FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" > now() - make_interval(days => $1) GROUP BY model ORDER BY s DESC NULLS LAST LIMIT 50', days)
        by_key = await conn.fetch(
            'SELECT COALESCE(v.key_alias, LEFT(l.api_key,10)) k, SUM(l.spend) s, COUNT(*) r, MAX(l."startTime") last '
            'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token = l.api_key '
            'WHERE l."startTime" > now() - make_interval(days => $1) GROUP BY k ORDER BY s DESC NULLS LAST LIMIT 50', days)
        daily = await conn.fetch(
            'SELECT date_trunc(\'day\', "startTime")::date d, COUNT(*) r, SUM(spend) s FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" > now() - make_interval(days => $1) GROUP BY d ORDER BY d', days)
    except Exception:
        # Do NOT silently return zeros — that disguises a broken query as "no usage".
        # Log loudly and flag it so the UI can say "couldn't load" instead of "empty".
        log.exception("usage_summary query failed (days=%s)", days)
        out = _shape_summary(days, {"spend": 0, "requests": 0, "tokens": 0}, [], [], [])
        out["error"] = "query_failed"
        return out
    finally:
        await conn.close()
    return _shape_summary(days, dict(totals), [dict(r) for r in by_model],
                          [dict(r) for r in by_key], [dict(r) for r in daily])
