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
    # store_model_in_db exposed (default False with no env set)
    assert d["store_model_in_db"] is False

def test_state_returns_store_model_in_db_true_when_env_set(monkeypatch, tmp_path):
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings
    get_settings.cache_clear()
    r = _client(tmp_path, FakeStore()).get("/api/config/state")
    assert r.json()["store_model_in_db"] is True
    get_settings.cache_clear()

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

def test_rendered_hybrid_is_settings_only(monkeypatch, tmp_path):
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings
    get_settings.cache_clear()
    # FakeStore has an applied model (router_setting + credential); in hybrid mode
    # the rendered config must omit model_list entries and credential_list.
    d=_client(tmp_path, FakeStore()).get("/api/config/rendered").json()["config"]
    assert d.get("model_list")==[]
    assert "credential_list" not in d
    get_settings.cache_clear()

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

# Task 1 (1.22.0): GET /api/config/drift

class FakeModelsClientRoutes:
    def __init__(self, live): self._live = live; self.added=[]; self.updated=[]; self.deleted=[]
    async def list_models(self): return self._live
    async def add_model(self, p): self.added.append(p); return {}
    async def update_model(self, p): self.updated.append(p); return {}
    async def delete_model(self, i): self.deleted.append(i); return {}

def _client_hybrid(tmp_path, store, live, monkeypatch):
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs
    gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: FakeModelsClientRoutes(live)
    return c

class ModelStore(FakeStore):
    def __init__(self, applied_models):
        self._applied = applied_models
        self._staged = []
        self.staged_calls=[]; self.cleared=None; self.folded=False

def _m(name, mid=None):
    return {"kind":"model","name":name,"data":{"model_name":name,"litellm_params":{"model":"openai/x"},"model_info":{"id":mid or name}}}

def test_drift_in_sync(tmp_path, monkeypatch):
    store = ModelStore([_m("a"), _m("b")])
    live = [{"model_name":"a","model_info":{"id":"a"}},{"model_name":"b","model_info":{"id":"b"}}]
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["hybrid"] is True and d["in_sync"] is True
    assert d["missing_in_litellm"]==[] and d["extra_in_litellm"]==[]

def test_drift_reports_missing_and_extra(tmp_path, monkeypatch):
    store = ModelStore([_m("a"), _m("b")])               # master wants a,b
    live = [{"model_name":"a","model_info":{"id":"a"}},{"model_name":"z","model_info":{"id":"z"}}]  # litellm has a,z
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["in_sync"] is False
    assert [x["id"] for x in d["missing_in_litellm"]] == ["b"]
    assert [x["id"] for x in d["extra_in_litellm"]] == ["z"]

def test_drift_config_only_is_na(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_MODEL_IN_DB", raising=False)
    from app.settings import get_settings as gs; gs.cache_clear()
    d = _client(tmp_path, ModelStore([_m("a")])).get("/api/config/drift").json()
    assert d["hybrid"] is False and d["in_sync"] is True

def test_drift_query_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs; gs.cache_clear()
    c = _client(tmp_path, ModelStore([_m("a")]))
    import app.routes.config_v3_routes as cr
    class _Raises:
        async def list_models(self): raise RuntimeError("boom")
    cr.make_models_client = lambda: _Raises()
    assert c.get("/api/config/drift").json()["error"] == "query_failed"
    gs.cache_clear()

def test_resync_adds_missing(tmp_path, monkeypatch):
    store = ModelStore([_m("a"), _m("b")])
    fake = FakeModelsClientRoutes([{"model_name":"a","model_info":{"id":"a"}}])  # missing b
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs; gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: fake
    r = c.post("/api/config/resync").json()
    assert r["added"] == 1 and r["deleted"] == 0
    assert fake.added[0]["model_info"]["id"] == "b"

def test_resync_deletes_extra(tmp_path, monkeypatch):
    store = ModelStore([_m("a")])
    fake = FakeModelsClientRoutes([{"model_name":"a","model_info":{"id":"a"}},
                                   {"model_name":"z","model_info":{"id":"z"}}])  # extra z
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs; gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: fake
    r = c.post("/api/config/resync").json()
    assert r["deleted"] == 1 and fake.deleted == ["z"]

def test_resync_requires_hybrid(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_MODEL_IN_DB", raising=False)
    from app.settings import get_settings as gs; gs.cache_clear()
    assert _client(tmp_path, ModelStore([_m("a")])).post("/api/config/resync").status_code == 422

# Task 4 (1.23.0): content_drifted in drift + converge_content in resync

def _m_mi(name, model_info):
    return {"kind": "model", "name": name,
            "data": {"model_name": name, "litellm_params": {"model": "openai/x"}, "model_info": dict(model_info)}}


def test_drift_reports_content_drift(tmp_path, monkeypatch):
    store = ModelStore([_m_mi("a", {"id": "a", "disable_background_health_check": True})])
    live = [{"model_name": "a", "model_info": {"id": "a"}}]   # present but flag absent → content drift
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["in_sync"] is False
    assert d["missing_in_litellm"] == [] and d["extra_in_litellm"] == []
    assert d["content_drifted"] == [{"id": "a", "model_name": "a",
                                     "fields": ["disable_background_health_check"]}]


def test_drift_content_in_sync_when_flag_matches(tmp_path, monkeypatch):
    store = ModelStore([_m_mi("a", {"id": "a", "disable_background_health_check": True})])
    live = [{"model_name": "a", "model_info": {"id": "a", "disable_background_health_check": True}}]
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["in_sync"] is True and d["content_drifted"] == []


def test_resync_updates_content_drift(tmp_path, monkeypatch):
    store = ModelStore([_m_mi("a", {"id": "a", "disable_background_health_check": True})])
    fake = FakeModelsClientRoutes([{"model_name": "a", "model_info": {"id": "a"}}])  # flag absent
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs; gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: fake
    r = c.post("/api/config/resync").json()
    assert r["updated"] == 1 and r["added"] == 0 and r["deleted"] == 0
    assert fake.updated[0]["model_info"]["id"] == "a"
    gs.cache_clear()


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

# Task 2: GET /api/config/integrity

class FakeStoreWithModels(FakeStore):
    def __init__(self):
        super().__init__()
        self._applied = [
            {"kind": "model", "name": "id1", "data": {"model_name": "gpt-oss-20b-1x"}},
            {"kind": "router_setting", "name": "fallbacks", "data": [{"gpt-oss-20b": ["gpt-oss-20b-1x"]}]},
        ]
        self._staged = []

class FakeKeysV3:
    def __init__(self, keys): self._keys = keys
    async def list_keys(self): return self._keys

def _client_integ(tmp_path, store, keys):
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_keys_client = lambda: FakeKeysV3(keys)
    return c

def test_integrity_reports_router_and_key_orphans(tmp_path):
    keys = [{"token": "h1", "key_alias": "ci", "models": ["deadgroup"], "aliases": {}}]
    d = _client_integ(tmp_path, FakeStoreWithModels(), keys).get("/api/config/integrity").json()
    assert d["in_sync"] is False
    assert any(o["reference"] == "gpt-oss-20b" for o in d["router_orphans"])   # fallback primary dead
    assert any(o["reference"] == "deadgroup" for o in d["key_orphans"])

def test_integrity_key_store_failure_is_loud(tmp_path):
    class Boom:
        async def list_keys(self): raise RuntimeError("proxy down")
    c = _client(tmp_path, FakeStoreWithModels())
    import app.routes.config_v3_routes as cr
    cr.make_keys_client = lambda: Boom()
    d = c.get("/api/config/integrity").json()
    assert d["error"] == "query_failed"
    assert d["router_orphans"]   # router-scope findings survive a key-store failure

def test_apply_route_returns_422_on_integrity_gate(tmp_path):
    r = _client(tmp_path, FakeStoreWithModels()).post("/api/apply")
    assert r.status_code == 422 and "integrity" in r.json()["detail"]

# Task 4: POST /api/config/integrity/fix

class FakeKeysFix(FakeKeysV3):
    def __init__(self, keys): super().__init__(keys); self.updated = None
    async def update_key(self, payload): self.updated = payload; return {"updated": True, **payload}

def test_fix_router_dry_run_previews_without_staging(tmp_path):
    store = FakeStoreWithModels()
    c = _client_integ(tmp_path, store, keys=[])
    orphan = {"scope": "router", "target": {"setting": "fallbacks", "primary": "gpt-oss-20b", "dangling": "gpt-oss-20b"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": True}).json()
    assert d["before"] == [{"gpt-oss-20b": ["gpt-oss-20b-1x"]}] and d["after"] == []
    assert "Apply" in d["effect"] and store.staged_calls == []       # nothing mutated on dry-run

def test_fix_router_stages_delete_when_setting_empties(tmp_path):
    store = FakeStoreWithModels()
    c = _client_integ(tmp_path, store, keys=[])
    orphan = {"scope": "router", "target": {"setting": "fallbacks", "primary": "gpt-oss-20b", "dangling": "gpt-oss-20b"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": False}).json()
    assert d == {"staged": True, "needs_apply": True}
    assert store.staged_calls == [("router_setting", "fallbacks", {}, True)]   # emptied → staged delete

def test_fix_key_updates_hot(tmp_path):
    keys = [{"token": "h1", "key_alias": "ci", "models": ["gpt-oss-20b-1x", "deadgroup"], "aliases": {}}]
    store = FakeStoreWithModels()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    fake = FakeKeysFix(keys); cr.make_keys_client = lambda: fake
    orphan = {"scope": "key", "target": {"token": "h1", "field": "models", "entry": "deadgroup"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": False}).json()
    assert d == {"applied": True, "needs_apply": False}
    assert fake.updated == {"key": "h1", "models": ["gpt-oss-20b-1x"]}   # trimmed, replace semantics

def test_fix_key_dry_run_does_not_update(tmp_path):
    keys = [{"token": "h1", "key_alias": "ci", "models": ["gpt-oss-20b-1x", "deadgroup"], "aliases": {}}]
    store = FakeStoreWithModels()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    fake = FakeKeysFix(keys); cr.make_keys_client = lambda: fake
    orphan = {"scope": "key", "target": {"token": "h1", "field": "models", "entry": "deadgroup"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": True}).json()
    assert d["before"] == ["gpt-oss-20b-1x", "deadgroup"] and d["after"] == ["gpt-oss-20b-1x"]
    assert fake.updated is None                          # dry-run mutates nothing

def test_fix_router_stages_trimmed_when_rule_survives(tmp_path):
    store = FakeStoreWithModels()
    store._applied = [
        {"kind": "model", "name": "id1", "data": {"model_name": "gpt-oss-20b-1x"}},
        {"kind": "router_setting", "name": "fallbacks",
         "data": [{"gpt-oss-20b-1x": ["gpt-oss-20b-1x", "deadgroup"]}]},
    ]
    c = _client_integ(tmp_path, store, keys=[])
    orphan = {"scope": "router", "target": {"setting": "fallbacks", "primary": "gpt-oss-20b-1x", "dangling": "deadgroup"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": False}).json()
    assert d == {"staged": True, "needs_apply": True}
    # rule survives (one target left) → stages the trimmed value, NOT a delete
    assert store.staged_calls == [("router_setting", "fallbacks", [{"gpt-oss-20b-1x": ["gpt-oss-20b-1x"]}], False)]

def test_fix_key_missing_token_409(tmp_path):
    store = FakeStoreWithModels()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_keys_client = lambda: FakeKeysFix([])       # no keys → token not found
    orphan = {"scope": "key", "target": {"token": "nope", "field": "models", "entry": "deadgroup"}}
    assert c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": False}).status_code == 409

def test_fix_unknown_scope_422(tmp_path):
    c = _client_integ(tmp_path, FakeStoreWithModels(), keys=[])
    r = c.post("/api/config/integrity/fix", json={"orphan": {"scope": "bogus", "target": {}}, "dry_run": False})
    assert r.status_code == 422
