import json, httpx, pytest
from app.models_client import ModelsClient


def _client(handler):
    return ModelsClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_models_unwraps_data_and_auth():
    def handler(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        assert req.url.path.endswith("/model/info")
        return httpx.Response(200, json={"data": [{"model_name": "gpt", "model_info": {"id": "uuid-1"}}]})
    out = await _client(handler).list_models()
    assert out[0]["model_info"]["id"] == "uuid-1"


@pytest.mark.asyncio
async def test_add_model_posts_to_model_new():
    seen = {}
    def handler(req):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"model_id": "uuid-1"})
    await _client(handler).add_model({"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {"id": "uuid-1"}})
    assert seen["path"].endswith("/model/new")
    assert seen["body"]["model_info"]["id"] == "uuid-1"


@pytest.mark.asyncio
async def test_update_model_patches_to_model_id_update():
    seen = {}
    def handler(req):
        seen["method"] = req.method
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"model_id": "uuid-1"})
    await _client(handler).update_model({"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o-mini"}, "model_info": {"id": "uuid-1"}})
    assert seen["method"] == "PATCH"
    assert seen["path"].endswith("/model/uuid-1/update")
    assert seen["body"]["model_info"]["id"] == "uuid-1"


@pytest.mark.asyncio
async def test_delete_model_posts_id():
    seen = {}
    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"deleted": True})
    await _client(handler).delete_model("uuid-1")
    assert seen["body"] == {"id": "uuid-1"}


@pytest.mark.asyncio
async def test_error_raises():
    def handler(req):
        return httpx.Response(500, text="boom")
    with pytest.raises(httpx.HTTPError):
        await _client(handler).list_models()


def _capture():
    seen = {}
    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content.decode())
        return httpx.Response(200, json={"ok": True})
    return seen, httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_update_model_uses_patch_endpoint_with_explicit_managed_fields():
    seen, transport = _capture()
    c = ModelsClient("http://proxy:4000", "sk-master", transport=transport)
    # desired model_info OMITS the managed field (un-ticked) — PATCH must still send it explicitly as False
    await c.update_model({"model_name": "m", "litellm_params": {"model": "openai/x"},
                          "model_info": {"id": "abc-123"}})
    assert seen["method"] == "PATCH"
    assert seen["url"] == "http://proxy:4000/model/abc-123/update"
    assert seen["body"]["model_info"]["disable_background_health_check"] is False


@pytest.mark.asyncio
async def test_update_model_preserves_explicit_true():
    seen, transport = _capture()
    c = ModelsClient("http://proxy:4000", "sk-master", transport=transport)
    await c.update_model({"model_name": "m", "litellm_params": {"model": "openai/x"},
                          "model_info": {"id": "abc-123", "disable_background_health_check": True}})
    assert seen["body"]["model_info"]["disable_background_health_check"] is True


@pytest.mark.asyncio
async def test_update_model_requires_id():
    _, transport = _capture()
    c = ModelsClient("http://proxy:4000", "sk-master", transport=transport)
    with pytest.raises(ValueError):
        await c.update_model({"model_name": "m", "litellm_params": {}, "model_info": {}})
