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

# Task 1 v3.6: _credential_data helper (keep-existing-key when blank)

class _FakeStore:
    def __init__(self, applied): self._applied = applied
    async def applied(self): return self._applied
    async def staged(self): return []

async def test_credential_data_keeps_existing_key_when_blank():
    from app.routes.config_v3_routes import _credential_data
    store = _FakeStore([{"kind": "credential", "name": "DUMMY",
                         "data": {"provider": "old", "value_encrypted": "ENC123"}}])
    out = await _credential_data("DUMMY", {"provider": "openai_compatible", "api_key": ""}, store)
    assert out == {"provider": "openai_compatible", "value_encrypted": "ENC123"}

async def test_credential_data_rejects_blank_for_new_credential():
    import pytest
    from fastapi import HTTPException
    from app.routes.config_v3_routes import _credential_data
    with pytest.raises(HTTPException):
        await _credential_data("NEW", {"provider": "x", "api_key": ""}, _FakeStore([]))

async def test_credential_data_encrypts_a_provided_key():
    import os
    os.environ.setdefault("SESSION_SECRET", "testsecret")
    from app.routes.config_v3_routes import _credential_data
    out = await _credential_data("K", {"provider": "openai", "api_key": "sk-real"}, _FakeStore([]))
    assert out["provider"] == "openai" and out["value_encrypted"] and out["value_encrypted"] != "sk-real"

# Task 6: hybrid path chosen when STORE_MODEL_IN_DB=true

# Task 7: POST /api/config/prepare-hot-apply

def _client_prepare(tmp_path, store, ok=True):
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_reloader = lambda: FakeReloader(ok)
    return c

def test_prepare_hot_apply_writes_empty_model_list(tmp_path):
    s = FakeStore(); c = _client_prepare(tmp_path, s, ok=True)
    r = c.post("/api/config/prepare-hot-apply")
    assert r.status_code == 200
    assert r.json()["prepared"] is True
    import yaml
    written = yaml.safe_load(open(os.environ["CONFIG_PATH"]))
    assert written.get("model_list") == []
    assert "credential_list" not in written
    assert ("general_setting", "store_model_in_db", True, False) in s.staged_calls

# Task 8: GET /api/config/export

def test_export_returns_items_with_encrypted_credentials(tmp_path):
    s = FakeStore()
    c = _client(tmp_path, s)
    r = c.get("/api/config/export")
    assert r.status_code == 200
    d = r.json()
    assert d["version"] == 1
    assert isinstance(d["items"], list)
    # credential item must be present with value_encrypted intact
    cred = next((i for i in d["items"] if i["kind"] == "credential" and i["name"] == "openai"), None)
    assert cred is not None, "credential item missing from export"
    assert cred["data"]["value_encrypted"] == "ENC:sk-REAL", "encrypted value not preserved"
    # NO plaintext secret: the raw string "sk-REAL" must not appear outside the ENC: prefix
    # (the payload contains "ENC:sk-REAL" which is fine; assert no bare "sk-REAL" leak)
    import json
    payload_text = json.dumps(d)
    assert "api_key" not in payload_text, "plaintext api_key field leaked in export"
    # value_encrypted is the only place the credential data lives; ENC:sk-REAL is fine
    assert payload_text.count("sk-REAL") == payload_text.count("ENC:sk-REAL"), \
        "plaintext sk-REAL appears outside ENC: prefix"
    # Content-Disposition header
    cd = r.headers.get("content-disposition", "")
    assert "ui_config.json" in cd, f"Content-Disposition header missing ui_config.json: {cd!r}"

def test_export_requires_login(tmp_path):
    c = _client(tmp_path, FakeStore()); c.cookies.clear()
    assert c.get("/api/config/export").status_code == 401

# Task 6: hybrid path chosen when STORE_MODEL_IN_DB=true

def test_apply_uses_hybrid_when_store_model_in_db(monkeypatch, tmp_path):
    import app.routes.config_v3_routes as cr
    captured = {}
    async def fake_apply(config_path, store, reloader, *, decrypt, models_client=None, hybrid=False):
        captured["hybrid"] = hybrid
        captured["has_client"] = models_client is not None
        return {"applied": True, "hybrid": hybrid, "models": {"added": 0}, "restart": "skipped"}
    monkeypatch.setattr(cr, "apply_config", fake_apply)
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("pw"))
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "c.yaml"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.settings import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    cr.make_config_store = lambda: FakeStore()
    cr._fernet = lambda: _FakeFernet()
    cr.make_reloader = lambda: FakeReloader(ok=True)
    c = TestClient(create_app())
    c.post("/api/auth/login", json={"password": "pw"})
    resp = c.post("/api/apply")
    assert resp.status_code == 200
    assert captured.get("hybrid") is True
    assert captured.get("has_client") is True
