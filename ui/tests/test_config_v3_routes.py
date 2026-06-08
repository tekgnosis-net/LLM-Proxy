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
        self.staged_calls=[]; self.cleared=None; self.folded=False
    async def applied(self): return list(self._applied)
    async def staged(self): return list(self._staged)
    async def staged_count(self): return len(self._staged)
    async def stage(self, kind, name, data, *, deleted=False): self.staged_calls.append((kind,name,data,deleted))
    async def clear_staged(self, kind=None, name=None): self.cleared=(kind,name)
    async def fold(self): self.folded=True; self._staged=[]

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

def test_put_item_stages_plain(tmp_path):
    s=FakeStore(); c=_client(tmp_path, s)
    r=c.put("/api/config/item", json={"kind":"router_setting","name":"num_retries","data":3})
    assert r.status_code==200 and r.json()["pending"] is True
    assert ("router_setting","num_retries",3,False) in s.staged_calls

def test_put_item_credential_encrypts_key(tmp_path):
    s=FakeStore(); c=_client(tmp_path, s)
    r=c.put("/api/config/item", json={"kind":"credential","name":"anthropic","data":{"provider":"anthropic","api_key":"sk-NEW"}})
    assert r.status_code==200
    kind,name,data,deleted=s.staged_calls[-1]
    assert kind=="credential" and name=="anthropic" and deleted is False
    assert data["provider"]=="anthropic" and data["value_encrypted"]=="ENC:sk-NEW" and "api_key" not in data

def test_put_item_credential_requires_key(tmp_path):
    c=_client(tmp_path, FakeStore())
    assert c.put("/api/config/item", json={"kind":"credential","name":"x","data":{"provider":"openai"}}).status_code==422

def test_delete_item_stages_deleted(tmp_path):
    s=FakeStore(); c=_client(tmp_path, s)
    assert c.request("DELETE","/api/config/item/model/gpt").status_code==200
    assert ("model","gpt",{},True) in s.staged_calls

def test_item_requires_login(tmp_path):
    c=_client(tmp_path, FakeStore()); c.cookies.clear()
    assert c.put("/api/config/item", json={"kind":"router_setting","name":"x","data":1}).status_code==401

# Task 3: POST /api/apply + POST /api/discard + GET /api/config/rendered

class FakeReloader:
    def __init__(self, ok=True): self.ok=ok
    async def reload_and_verify(self, expected):
        if not self.ok:
            from app.reloader import ReloadError; raise ReloadError("sim")
        return True

def _client_apply(tmp_path, store, ok=True):
    c=_client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_reloader = lambda: FakeReloader(ok)
    return c

def test_apply_ok(tmp_path):
    s=FakeStore(); c=_client_apply(tmp_path, s, ok=True)
    r=c.post("/api/apply"); assert r.status_code==200 and r.json()["applied"] is True and r.json()["servant"]=="healthy"
    assert s.folded is True
    import yaml; assert yaml.safe_load(open(os.environ["CONFIG_PATH"]))["router_settings"]["routing_strategy"]=="least-busy"

def test_apply_servant_unhealthy_200_committed(tmp_path):
    s=FakeStore(); c=_client_apply(tmp_path, s, ok=False)
    r=c.post("/api/apply"); assert r.status_code==200 and r.json()["servant"]=="unhealthy" and s.folded is True

def test_discard_all(tmp_path):
    s=FakeStore(); c=_client(tmp_path, s)
    r=c.post("/api/discard"); assert r.status_code==200 and s.cleared==(None,None)

def test_discard_one(tmp_path):
    s=FakeStore(); c=_client(tmp_path, s)
    c.post("/api/discard?kind=router_setting&name=routing_strategy"); assert s.cleared==("router_setting","routing_strategy")

def test_rendered_redacted(tmp_path):
    d=_client(tmp_path, FakeStore()).get("/api/config/rendered").json()
    # credential rendered then redacted
    assert d["config"]["credential_list"][0]["credential_values"]["api_key"]=="***"
    assert d["config"]["router_settings"]["routing_strategy"]=="least-busy"

# Task 4: GET/PUT /api/config/passthrough

def test_get_passthrough_empty(tmp_path):
    d=_client(tmp_path, FakeStore()).get("/api/config/passthrough").json()
    assert d["yaml"] == "" or d["yaml"] == "{}\n" or d["data"] == {}

def test_put_passthrough_parses_and_stages(tmp_path):
    s=FakeStore(); c=_client(tmp_path, s)
    r=c.put("/api/config/passthrough", json={"yaml":"callbacks:\n  - langfuse\n"})
    assert r.status_code==200
    kind,name,data,deleted=s.staged_calls[-1]
    assert kind=="passthrough" and name=="_" and data=={"callbacks":["langfuse"]} and deleted is False

def test_put_passthrough_bad_yaml_422(tmp_path):
    c=_client(tmp_path, FakeStore())
    assert c.put("/api/config/passthrough", json={"yaml":"key: [unterminated"}).status_code==422
