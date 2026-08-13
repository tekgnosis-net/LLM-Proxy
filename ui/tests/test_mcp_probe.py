import base64
import json, httpx, pytest
from app.mcp_probe import ProbeError, build_probe_headers, parse_rpc_response, probe_tools


def test_build_probe_headers_auth_matrix():
    assert build_probe_headers("bearer_token", "tok", {})["Authorization"] == "Bearer tok"
    assert build_probe_headers("basic", "user:pass", {})["Authorization"] == \
        "Basic " + base64.b64encode(b"user:pass").decode()
    assert build_probe_headers("api_key", "k1", {})["X-API-Key"] == "k1"
    h = build_probe_headers(None, None, {"X-Extra": "1"})
    assert h["X-Extra"] == "1" and "Authorization" not in h
    assert h["Accept"] == "application/json, text/event-stream"
    # blank value with auth_type set → no auth header (route resolves stored secret first)
    assert "Authorization" not in build_probe_headers("bearer_token", "", {})
    # static_headers overlay LAST — static wins on conflict (gateway ordering)
    assert build_probe_headers("bearer_token", "tok", {"Authorization": "Custom x"})["Authorization"] == "Custom x"


def test_parse_rpc_response_sse_and_json():
    sse = httpx.Response(200, headers={"content-type": "text/event-stream"},
                         text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n')
    assert parse_rpc_response(sse)["result"] == {"ok": True}
    js = httpx.Response(200, headers={"content-type": "application/json"},
                        text='{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')
    assert parse_rpc_response(js)["result"] == {"ok": True}
    empty = httpx.Response(202)
    assert parse_rpc_response(empty) is None
    bad = httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: {nope\n")
    with pytest.raises(ProbeError):
        parse_rpc_response(bad)


def _rpc_server(tools, require_session=False, init_status=200, sse=True):
    """MockTransport for the 3-step probe. Returns (transport, seen)."""
    seen = {"headers": [], "methods": []}
    def respond(payload):
        if sse:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text=f"data: {json.dumps(payload)}\n\n")
        return httpx.Response(200, json=payload)
    def handler(req):
        body = json.loads(req.content) if req.content else {}
        seen["headers"].append(dict(req.headers))
        seen["methods"].append(body.get("method"))
        if body.get("method") == "initialize":
            r = respond({"jsonrpc": "2.0", "id": 1,
                         "result": {"serverInfo": {"name": "t", "version": "1"}}})
            if require_session:
                r.headers["mcp-session-id"] = "sess-1"
            if init_status != 200:
                return httpx.Response(init_status, text="denied")
            return r
        if body.get("method") == "notifications/initialized":
            if require_session and req.headers.get("mcp-session-id") != "sess-1":
                return httpx.Response(400, text="missing session")
            return httpx.Response(202)
        if body.get("method") == "tools/list":
            if require_session and req.headers.get("mcp-session-id") != "sess-1":
                return httpx.Response(400, text="missing session")
            return respond({"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}})
        return httpx.Response(404)
    return httpx.MockTransport(handler), seen


@pytest.mark.asyncio
async def test_probe_happy_path_sse():
    t, seen = _rpc_server([{"name": "a", "description": "da"}, {"name": "b"}])
    out = await probe_tools("http://x/mcp", http_transport=t)
    assert out == [{"name": "a", "description": "da"}, {"name": "b", "description": ""}]
    assert seen["methods"] == ["initialize", "notifications/initialized", "tools/list"]


@pytest.mark.asyncio
async def test_probe_happy_path_plain_json_and_session_echo():
    t, seen = _rpc_server([{"name": "a"}], require_session=True, sse=False)
    out = await probe_tools("http://x/mcp", http_transport=t)
    assert out[0]["name"] == "a"
    assert seen["headers"][2].get("mcp-session-id") == "sess-1"


@pytest.mark.asyncio
async def test_probe_auth_header_sent():
    t, seen = _rpc_server([{"name": "a"}])
    await probe_tools("http://x/mcp", auth_type="bearer_token", auth_value="tok", http_transport=t)
    assert seen["headers"][0].get("authorization") == "Bearer tok"


@pytest.mark.asyncio
async def test_probe_401_maps_to_probe_error():
    t, _ = _rpc_server([], init_status=401)
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/mcp", http_transport=t)
    assert "401" in str(ei.value)


@pytest.mark.asyncio
async def test_probe_redirect_maps_to_probe_error():
    def handler(req):
        body = json.loads(req.content) if req.content else {}
        if body.get("method") == "initialize":
            return httpx.Response(307, headers={"location": "http://evil/mcp"})
        return httpx.Response(404)
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/mcp", http_transport=httpx.MockTransport(handler))
    assert "redirected" in str(ei.value)
    assert "evil" not in str(ei.value)


@pytest.mark.asyncio
async def test_probe_rpc_error_object():
    def handler(req):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "error": {"code": -32000, "message": "boom"}})
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/mcp", http_transport=httpx.MockTransport(handler))
    assert "boom" in str(ei.value)


@pytest.mark.asyncio
async def test_probe_rejects_non_http_transport():
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/mcp", transport="sse")
    assert "HTTP transport" in str(ei.value)


@pytest.mark.asyncio
async def test_probe_connect_error_hides_url():
    def handler(req):
        raise httpx.ConnectError("secret-url-inside")
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/sk-EMBEDDED/mcp", http_transport=httpx.MockTransport(handler))
    assert "sk-EMBEDDED" not in str(ei.value) and "ConnectError" in str(ei.value)
