import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path/"c.yaml"), DATABASE_URL="postgresql://x")
    (tmp_path/"c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.credentials_routes as cr
    cr.make_credentials_store = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password":"pw"}); return c


class FakeStore:
    def __init__(self): self.created=None; self.deleted=None
    async def list_masked(self): return [{"credential_name":"openai","provider":"openai"}]
    async def list_decrypted(self): return [{"credential_name":"openai","provider":"openai","api_key":"sk-REAL"}]
    async def create(self,n,p,k): self.created=(n,p,k)
    async def delete(self,n): self.deleted=n


def test_requires_login(tmp_path):
    c=_client(tmp_path,FakeStore()); c.cookies.clear(); assert c.get("/api/credentials").status_code==401

def test_list_masked_no_values(tmp_path):
    r=_client(tmp_path,FakeStore()).get("/api/credentials")
    assert r.json()[0]["credential_name"]=="openai" and "api_key" not in r.json()[0] and "value_encrypted" not in r.json()[0]

def test_create_then_materialized_into_config(tmp_path):
    f=FakeStore(); c=_client(tmp_path,f)
    r=c.post("/api/credentials", json={"credential_name":"openai","provider":"openai","api_key":"sk-REAL"})
    assert r.status_code==200 and f.created==("openai","openai","sk-REAL")
    # config.yaml now has credential_list with the literal (materialized), and pending
    import yaml; d=yaml.safe_load(open(os.environ["CONFIG_PATH"]))
    assert d["credential_list"][0]["credential_values"]["api_key"]=="sk-REAL"
    assert r.json()["pending"] is True

def test_get_config_redacts_credential_values(tmp_path):
    f=FakeStore(); c=_client(tmp_path,f)
    c.post("/api/credentials", json={"credential_name":"openai","provider":"openai","api_key":"sk-REAL"})
    cfg=c.get("/api/config").json()
    assert cfg["credential_list"][0]["credential_values"]["api_key"]=="***"   # never leak to browser

def test_delete(tmp_path):
    f=FakeStore(); c=_client(tmp_path,f); assert c.request("DELETE","/api/credentials/openai").status_code==200 and f.deleted=="openai"
