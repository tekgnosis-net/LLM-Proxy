import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password

def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path/"c.yaml"), DATABASE_URL="postgresql://x")
    (tmp_path/"c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.catalog_routes as cat
    cat.make_catalog = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password":"pw"}); return c

class FakeCatalog:
    async def get_model(self,n): return {"model_name":n,"input_cost_per_token":2.5e-6,"output_cost_per_token":1e-5,"mode":"chat","max_input_tokens":128000} if n=="gpt-4o" else None
    async def get_providers(self): return [{"provider":"openai","display_name":"OpenAI","endpoints":{"chat_completions":True}}]
    async def status(self): return {"last_synced":"2026-06-07T00:00:00Z","models":2775,"providers":157,"last_error":None}
    async def sync(self): return {"models":2775,"providers":157}

def test_requires_login(tmp_path):
    c=_client(tmp_path,FakeCatalog()); c.cookies.clear(); assert c.get("/api/catalog/status").status_code==401
def test_get_model(tmp_path):
    r=_client(tmp_path,FakeCatalog()).get("/api/catalog/model/gpt-4o"); assert r.json()["mode"]=="chat"
def test_get_model_404(tmp_path):
    assert _client(tmp_path,FakeCatalog()).get("/api/catalog/model/unknown").status_code==404
def test_providers(tmp_path):
    assert _client(tmp_path,FakeCatalog()).get("/api/catalog/providers").json()[0]["provider"]=="openai"
def test_status_and_sync(tmp_path):
    c=_client(tmp_path,FakeCatalog())
    assert c.get("/api/catalog/status").json()["models"]==2775
    assert c.post("/api/catalog/sync").json()["models"]==2775
