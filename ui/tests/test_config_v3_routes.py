import os
from fastapi.testclient import TestClient
from app.auth import hash_password

def _client(tmp_path, store):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path/"c.yaml"), DATABASE_URL="postgresql://x")
    (tmp_path/"c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.config_v3_routes as cr
    cr.make_config_store = lambda: store
    cr._fernet = lambda: _FakeFernet()
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password":"pw"}); return c

class _FakeFernet:
    def encrypt(self, b): return b"ENC:"+b
    def decrypt(self, b): return b[4:] if b.startswith(b"ENC:") else b

class FakeStore:
    def __init__(self):
        self._applied=[{"kind":"router_setting","name":"routing_strategy","data":"simple-shuffle"},
                       {"kind":"credential","name":"openai","data":{"provider":"openai","value_encrypted":"ENC:sk-REAL"}}]
        self._staged=[{"kind":"router_setting","name":"routing_strategy","data":"least-busy","flag":"changed"}]
        self.staged_calls=[]; self.cleared=None
    async def applied(self): return list(self._applied)
    async def staged(self): return list(self._staged)
    async def staged_count(self): return len(self._staged)
    async def stage(self, kind, name, data, *, deleted=False): self.staged_calls.append((kind,name,data,deleted))
    async def clear_staged(self, kind=None, name=None): self.cleared=(kind,name)

def test_state_requires_login(tmp_path):
    c=_client(tmp_path, FakeStore()); c.cookies.clear(); assert c.get("/api/config/state").status_code==401

def test_state_returns_effective_with_flags_redacted(tmp_path):
    r=_client(tmp_path, FakeStore()).get("/api/config/state"); d=r.json()
    assert d["pending"] is True and d["count"]==1
    items={(i["kind"],i["name"]):i for i in d["items"]}
    assert items[("router_setting","routing_strategy")]["data"]=="least-busy"
    assert items[("router_setting","routing_strategy")]["flag"]=="changed"
    # credential redacted: no value_encrypted / plaintext leaked
    cred=items[("credential","openai")]
    assert cred["data"].get("provider")=="openai"
    assert "value_encrypted" not in cred["data"] and cred["data"].get("api_key") in (None,"***")
