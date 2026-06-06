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
