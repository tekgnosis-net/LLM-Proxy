import httpx, pytest
from app.keys_client import KeysClient


def _client(handler):
    return KeysClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_keys_uses_full_object_and_auth():
    def handler(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        assert req.url.params.get("return_full_object") == "true"
        return httpx.Response(200, json={"keys": [{"token": "h1", "key_alias": "ci", "spend": 0.5, "max_budget": 10}], "total_count": 1})
    keys = await _client(handler).list_keys()
    assert keys[0]["key_alias"] == "ci"


@pytest.mark.asyncio
async def test_generate_key_returns_plaintext_once():
    def handler(req):
        assert req.url.path.endswith("/key/generate")
        return httpx.Response(200, json={"key": "sk-NEWPLAINTEXT", "token": "hashed", "key_alias": "ci", "max_budget": 10})
    res = await _client(handler).generate_key({"key_alias": "ci", "max_budget": 10})
    assert res["key"] == "sk-NEWPLAINTEXT"


@pytest.mark.asyncio
async def test_delete_keys_posts_tokens():
    seen = {}
    def handler(req):
        if req.url.path.endswith("/key/delete"):
            import json
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"deleted_keys": ["h1"]})
        return httpx.Response(404)
    res = await _client(handler).delete_keys(["h1"])
    assert seen["body"] == {"keys": ["h1"]}
    assert res["deleted_keys"] == ["h1"]


@pytest.mark.asyncio
async def test_list_keys_error_raises():
    def handler(req):
        return httpx.Response(500, text="boom")
    with pytest.raises(httpx.HTTPError):
        await _client(handler).list_keys()
