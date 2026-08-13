import json, httpx, pytest
from app.mcp_client import McpClient


def _client(handler):
    return McpClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_servers_auth_and_unwrap():
    def handler(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        assert req.url.path == "/v1/mcp/server"
        return httpx.Response(200, json=[{"server_id": "u1", "server_name": "deepwiki", "credentials": None}])
    out = await _client(handler).list_servers()
    assert out[0]["server_id"] == "u1"


@pytest.mark.asyncio
async def test_list_servers_unwraps_data_envelope():
    def handler(req):
        return httpx.Response(200, json={"data": [{"server_id": "u1"}]})
    out = await _client(handler).list_servers()
    assert out == [{"server_id": "u1"}]


@pytest.mark.asyncio
async def test_add_server_posts_payload():
    seen = {}
    def handler(req):
        seen["method"], seen["path"], seen["body"] = req.method, req.url.path, json.loads(req.content)
        return httpx.Response(200, json={"server_id": "u1"})
    await _client(handler).add_server({"server_id": "u1", "server_name": "s", "url": "http://x/mcp"})
    assert seen["method"] == "POST" and seen["path"] == "/v1/mcp/server"
    assert seen["body"]["server_id"] == "u1"


@pytest.mark.asyncio
async def test_update_server_puts_and_requires_id():
    seen = {}
    def handler(req):
        seen["method"], seen["path"] = req.method, req.url.path
        return httpx.Response(200, json={"server_id": "u1"})
    c = _client(handler)
    await c.update_server({"server_id": "u1", "url": "http://x/mcp"})
    assert seen["method"] == "PUT" and seen["path"] == "/v1/mcp/server"
    with pytest.raises(ValueError):
        await c.update_server({"url": "http://x/mcp"})


@pytest.mark.asyncio
async def test_delete_server_quotes_id_and_tolerates_empty_body():
    seen = {}
    def handler(req):
        seen["method"], seen["path"] = req.method, req.url.raw_path.decode()
        return httpx.Response(200)   # empty body
    out = await _client(handler).delete_server("u 1")
    assert seen["method"] == "DELETE" and seen["path"] == "/v1/mcp/server/u%201"
    assert out == {}


@pytest.mark.asyncio
async def test_health_and_tools_params():
    seen = {}
    def handler(req):
        seen["path"], seen["query"] = req.url.path, str(req.url.query, "utf-8")
        return httpx.Response(200, json={"ok": True})
    c = _client(handler)
    await c.health(["a", "b"])
    assert seen["path"] == "/v1/mcp/server/health" and "server_ids=a" in seen["query"] and "server_ids=b" in seen["query"]
    await c.list_tools("u1")
    assert seen["path"] == "/mcp-rest/tools/list" and "server_id=u1" in seen["query"]


@pytest.mark.asyncio
async def test_error_raises():
    def handler(req):
        return httpx.Response(500, text="boom")
    with pytest.raises(httpx.HTTPError):
        await _client(handler).list_servers()


@pytest.mark.asyncio
async def test_team_endpoints():
    seen = {}
    def handler(req):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(200, json={"ok": True})
    c = _client(handler)
    await c.new_team({"team_id": "ui-mcp"})
    assert seen["path"] == "/team/new"
    await c.update_team({"team_id": "ui-mcp", "object_permission": {"mcp_servers": ["a"]}})
    assert seen["path"] == "/team/update" and seen["body"]["object_permission"]["mcp_servers"] == ["a"]
