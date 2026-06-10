import types
import pytest
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


@pytest.mark.asyncio
async def test_usage_summary_binds_days_as_int(monkeypatch):
    """Regression: the window must be bound as an INTEGER day count (make_interval(days => $1)).
    Binding a string like '30 days' to $1::interval raises asyncpg DataError, which the silent
    catch turned into an all-zeros 'no usage' screen on a DB that actually had 900+ rows."""
    import app.routes.usage_routes as ur
    seen_args = []

    class FakeConn:
        async def fetchrow(self, q, *args):
            seen_args.append(args)
            return {"spend": 0, "requests": 0, "tokens": 0}
        async def fetch(self, q, *args):
            seen_args.append(args)
            return []
        async def close(self):
            pass

    async def fake_connect(dsn):
        return FakeConn()

    monkeypatch.setattr(ur.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(ur, "get_settings", lambda: types.SimpleNamespace(database_url="postgres://x/y"))

    await ur.usage_summary(days=30)

    assert seen_args, "no DB queries were run"
    # every query must receive the int day-count (30), never the str '30 days'
    assert all(args == (30,) for args in seen_args), seen_args
