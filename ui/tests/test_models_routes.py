import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s", CONFIG_PATH=str(tmp_path/"c.yaml"))
    (tmp_path/"c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.models_routes as mr
    mr.make_models_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password":"pw"}); return c


class FakeModels:
    async def test_connection(self, lp, mode): return {"status":"success","result":{"ok":True}}
    async def health_all(self): return {"healthy_endpoints":[{"model":"gpt-4o"}],"unhealthy_endpoints":[],"healthy_count":1,"unhealthy_count":0}


def test_test_requires_login(tmp_path):
    c=_client(tmp_path,FakeModels()); c.cookies.clear()
    assert c.post("/api/models/test", json={"litellm_params":{"model":"openai/gpt-4o"}}).status_code==401


def test_test_connection(tmp_path):
    r=_client(tmp_path,FakeModels()).post("/api/models/test", json={"litellm_params":{"model":"openai/gpt-4o","api_key":"sk-x"},"mode":"chat"})
    assert r.status_code==200 and r.json()["status"]=="success"


def test_health(tmp_path):
    assert _client(tmp_path,FakeModels()).get("/api/models/health").json()["healthy_count"]==1


def test_test_422_missing_model(tmp_path):
    r=_client(tmp_path,FakeModels()).post("/api/models/test", json={"litellm_params":{}})
    assert r.status_code==422


def test_health_requires_login(tmp_path):
    c=_client(tmp_path,FakeModels()); c.cookies.clear()
    assert c.get("/api/models/health").status_code==401


def test_test_connection_502_on_upstream_error(tmp_path):
    class Boom:
        async def test_connection(self, lp, mode): raise RuntimeError("conn refused")
        async def health_all(self): return {}
    c = _client(tmp_path, Boom())
    assert c.post("/api/models/test", json={"litellm_params": {"model": "openai/gpt-4o"}}).status_code == 502


def test_health_502_on_upstream_error(tmp_path):
    class Boom:
        async def test_connection(self, lp, mode): return {}
        async def health_all(self): raise RuntimeError("down")
    c = _client(tmp_path, Boom())
    assert c.get("/api/models/health").status_code == 502
