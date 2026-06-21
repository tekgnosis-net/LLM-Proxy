import types, pytest
from datetime import datetime
from app.routes.usage_routes import _shape_summary

def _row(label, **kw):
    base = {"label": label, "requests": 10, "tok_in": 100, "tok_out": 20, "spend": 1.5,
            "cost_per_1m": 0.05, "p50_ms": 200, "p95_ms": 900, "err_pct": 2.5}
    base.update(kw); return base

def test_shape_summary_maps_everything():
    kpis = {"spend": 1.5, "requests": 10, "tok_in": 100, "tok_out": 20, "error_rate": 0.1,
            "avg_latency_ms": 880.6, "p95_latency_ms": 900.4, "cache_hit_rate": 0.25}
    out = _shape_summary(30, "day", kpis, [_row("deepinfra")], [_row("gpt-oss-20b")],
                         [_row("team-a", last_used=datetime(2026,6,19,9,0))],
                         # date_trunc() returns a naive timestamp (UTC wall-clock), not a date
                         [{"bucket": datetime(2026,6,19,0,0), "requests": 5, "spend": 0.3, "p95_ms": 700.7}])
    assert out["range_days"] == 30 and out["granularity"] == "day"
    assert out["kpis"]["avg_latency_ms"] == 881 and out["kpis"]["cache_hit_rate"] == 0.25
    assert out["by_provider"][0]["label"] == "deepinfra" and out["by_provider"][0]["p95_ms"] == 900
    # timestamps carry an explicit +00:00 so the browser converts UTC→local
    assert out["by_key"][0]["last_used"] == "2026-06-19T09:00:00+00:00"
    assert "last_used" not in out["by_provider"][0]
    assert out["timeseries"][0] == {"bucket": "2026-06-19T00:00:00+00:00", "requests": 5, "spend": 0.3, "p95_ms": 701}

def test_shape_summary_none_guards():
    kpis = {"spend": None, "requests": 0, "tok_in": None, "tok_out": None, "error_rate": None,
            "avg_latency_ms": None, "p95_latency_ms": None, "cache_hit_rate": None}
    out = _shape_summary(7, "hour", kpis, [], [], [], [])
    assert out["kpis"] == {"spend": 0.0, "requests": 0, "tok_in": 0, "tok_out": 0, "error_rate": 0.0,
                           "avg_latency_ms": None, "p95_latency_ms": None, "cache_hit_rate": None}
    assert out["by_provider"] == [] and out["timeseries"] == []

def test_shape_summary_row_cost_none():
    out = _shape_summary(7, "day", {}, [_row("x", cost_per_1m=None)], [], [], [])
    assert out["by_provider"][0]["cost_per_1m"] is None


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
            return {"spend": 0, "requests": 0, "tok_in": 0, "tok_out": 0,
                    "error_rate": None, "avg_latency_ms": None, "p95_latency_ms": None,
                    "cache_hit_rate": None}
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


# ---------------------------------------------------------------------------
# Task 2: _shape_recent tests
# ---------------------------------------------------------------------------
from app.routes.usage_routes import _shape_recent
from datetime import datetime as _dt

def test_shape_recent_maps_rows():
    rows = [{"time": _dt(2026,6,19,18,42,3), "model": "deepinfra/openai/gpt-oss-20b",
             "provider": "deepinfra", "key": "hindsight-cbr", "tok_in": 1200, "tok_out": 340,
             "latency_ms": 41200, "status": "success", "cache_hit": "True"}]
    out = _shape_recent(rows)
    r = out["recent"][0]
    assert r["time"] == "2026-06-19T18:42:03+00:00" and r["provider"] == "deepinfra"
    assert r["cache_hit"] is True and r["status"] == "success" and r["latency_ms"] == 41200

def test_shape_recent_cache_false_and_none():
    rows = [{"time": _dt(2026,6,19,1,0), "model":"m","provider":"groq","key":"k","tok_in":1,
             "tok_out":2,"latency_ms":500,"status":"success","cache_hit":"False"},
            {"time": _dt(2026,6,19,1,1), "model":"m","provider":"groq","key":"k","tok_in":1,
             "tok_out":2,"latency_ms":500,"status":"failure","cache_hit":None}]
    out = _shape_recent(rows)
    assert out["recent"][0]["cache_hit"] is False and out["recent"][1]["cache_hit"] is None
