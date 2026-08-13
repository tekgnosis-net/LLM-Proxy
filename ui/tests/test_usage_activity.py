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

# ── type filtering (MCP vs LLM) ─────────────────────────────────────────────
def test_activity_where_type_filters():
    sql_mcp, _ = ur._activity_where(7, type_="mcp")
    assert "l.call_type IN ('call_mcp_tool','list_mcp_tools')" in sql_mcp
    sql_llm, _ = ur._activity_where(7, type_="llm")
    assert "l.call_type IS NULL OR l.call_type NOT IN" in sql_llm
    sql_all, _ = ur._activity_where(7)
    assert "call_mcp_tool" not in sql_all


def test_shape_activity_row_mcp_fields():
    r = ur._shape_activity_row({"id": "r1", "time": None, "model": "", "provider": "", "key": "k",
                                "tok_in": 0, "tok_out": 0, "spend": 0, "latency_ms": 1,
                                "status": "success", "cache_hit": None, "call_type": "call_mcp_tool",
                                "mcp_server": "deepwiki", "mcp_tool": "read_wiki_structure"})
    assert r["mcp_server"] == "deepwiki" and r["mcp_tool"] == "read_wiki_structure"


def test_extract_mcp_from_metadata():
    meta = json.dumps({"mcp_tool_call_metadata": {
        "mcp_server_name": "deepwiki", "name": "read_wiki_structure",
        "arguments": {"repoName": "x"}, "result": {"ok": True}}})
    m = ur._extract_mcp(meta)
    assert m == {"server": "deepwiki", "tool": "read_wiki_structure",
                 "arguments": {"repoName": "x"}, "result": {"ok": True}}
    assert ur._extract_mcp(None) is None
    assert ur._extract_mcp("not json") is None
    assert ur._extract_mcp(json.dumps({})) is None
