# LLM-Proxy Admin UI — v3.2: Config API Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD (FastAPI routes over the v3.1 engine, fakes for store/reloader). Steps use `- [ ]`. **Branch: `v3-master-servant`.**

**Goal:** Expose the v3.1 Master/Servant engine over HTTP — a single item-based config API (`/api/config/*`) — and retire the superseded v2 config + credential routes.

**Architecture:** Thin FastAPI routes over `config_db.ConfigStore`, `config_render`, `config_engine`. Credentials are `kind='credential'` items (the route encrypts the plaintext key on stage; reads are redacted). Apply maps the engine's commit-at-write result to HTTP (200 applied incl. servant health, 422 invalid, 500 write/fold failure). On the branch the old UI temporarily can't reach these (it's rewired in v3.3); v3.2 is verified by API.

**Tech Stack:** FastAPI, asyncpg, Fernet. No new deps.

**Spec:** [`../specs/2026-06-08-llm-proxy-ui-v3-master-servant-config.md`](../specs/2026-06-08-llm-proxy-ui-v3-master-servant-config.md) (§ API). Built on v3.1 engine ([`2026-06-08-llm-proxy-ui-v3.1-config-engine.md`](2026-06-08-llm-proxy-ui-v3.1-config-engine.md)).

---

## File Structure
```
ui/app/routes/config_v3_routes.py  # CREATE: /api/config/* (state, item, apply, discard, passthrough, rendered)
ui/app/main.py                     # MODIFY: include config_v3_routes; REMOVE config_routes + credentials_routes
ui/app/routes/config_routes.py     # DELETE (superseded: GET/PUT /api/config, /api/apply, /api/apply/status, /api/discard, /api/cache/info)
ui/app/routes/credentials_routes.py# DELETE (superseded: credentials are now items)
ui/app/config_store.py / apply.py  # (v1/v2 file-diff bits become dead; leave or remove in v3.3 cleanup — KEEP this PR minimal)
ui/tests/test_config_v3_routes.py  # CREATE
ui/tests/test_config_routes.py / test_credentials_routes.py  # DELETE (test the removed routes)
```

**Seams (module-level, monkeypatched in tests):** `make_config_store()` → `ConfigStore(settings.database_url)`; `make_reloader()` (reuse the existing one); `_fernet()` → `fernet_from_secret(credentials_key or session_secret)`.

---

## Task 1: GET /api/config/state (TDD)

**Files:** Create `ui/app/routes/config_v3_routes.py`, `ui/tests/test_config_v3_routes.py`.

- [ ] **Step 1: failing test** (fake store seam + login helper mirroring `test_catalog_routes.py`):
```python
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
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement** `config_v3_routes.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.settings import get_settings
from app.config_db import ConfigStore
from app.config_render import effective, render_config, redact_rendered
from app.config_engine import apply_config, pending_status, ApplyError
from app.credentials_store import fernet_from_secret
from app.config_store import ConfigError

router = APIRouter(prefix="/api")

def make_config_store() -> ConfigStore:
    s = get_settings()
    if not s.database_url: raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return ConfigStore(s.database_url)

def _fernet():
    s = get_settings(); return fernet_from_secret(s.credentials_key or s.session_secret)

def _redact_item(it: dict) -> dict:
    if it["kind"] == "credential":
        d = it["data"] or {}
        return {**it, "data": {"provider": d.get("provider"), "api_key": "***"}}
    return it

@router.get("/config/state", dependencies=[Depends(login_required)])
async def config_state():
    store = make_config_store()
    try:
        eff = effective(await store.applied(), await store.staged())
        n = await store.staged_count()
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"config state error: {e}")
    return {"items": [_redact_item(i) for i in eff], "pending": n > 0, "count": n}
```
Wire `config_v3_routes.router` into `main.py`.

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): GET /api/config/state (effective items + flags, redacted)`.

---

## Task 2: PUT /api/config/item + DELETE /api/config/item/{kind}/{name} (TDD)

**Files:** Modify `config_v3_routes.py`, `test_config_v3_routes.py`.

- [ ] **Step 1: failing tests:**
```python
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
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement:**
```python
@router.put("/config/item", dependencies=[Depends(login_required)])
async def stage_item(body: dict = Body(...)):
    kind, name, data = body.get("kind"), body.get("name"), body.get("data")
    if not kind or not name: raise HTTPException(status_code=422, detail="kind and name required")
    if kind == "credential":
        api_key = (data or {}).get("api_key")
        if not api_key: raise HTTPException(status_code=422, detail="credential api_key required")
        data = {"provider": (data or {}).get("provider"),
                "value_encrypted": _fernet().encrypt(api_key.encode()).decode()}
    try:
        await make_config_store().stage(kind, name, data)
        return await pending_status(make_config_store())
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"stage error: {e}")

@router.delete("/config/item/{kind}/{name}", dependencies=[Depends(login_required)])
async def delete_item(kind: str, name: str):
    try:
        await make_config_store().stage(kind, name, {}, deleted=True)
        return await pending_status(make_config_store())
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"stage error: {e}")
```
(NOTE: credential update with no new key is out of scope — keys are write-only; re-entering is required, matching v2.2. Document in the response/UI later.)

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): PUT/DELETE /api/config/item (stage; credential key encrypted)`.

---

## Task 3: POST /api/apply + POST /api/discard + GET /api/config/rendered (TDD)

**Files:** Modify `config_v3_routes.py`, `test_config_v3_routes.py`.

- [ ] **Step 1: failing tests** (apply uses a fake reloader seam + the real engine over the FakeStore; the FakeStore needs `fold()`):
```python
# extend FakeStore: add  self.folded=False ;  async def fold(self): self.folded=True; self._staged=[]
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
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement:**
```python
@router.post("/apply", dependencies=[Depends(login_required)])
async def apply():
    s = get_settings(); f = _fernet()
    try:
        return await apply_config(s.config_path, make_config_store(), make_reloader(),
                                  decrypt=lambda b: f.decrypt(b.encode()).decode())
    except ApplyError as e:
        code = 422 if "invalid" in str(e) else 500
        raise HTTPException(status_code=code, detail=str(e))

@router.post("/discard", dependencies=[Depends(login_required)])
async def discard(kind: str | None = None, name: str | None = None):
    await make_config_store().clear_staged(kind, name)
    return await pending_status(make_config_store())

@router.get("/config/rendered", dependencies=[Depends(login_required)])
async def rendered():
    store = make_config_store(); f = _fernet()
    eff = effective(await store.applied(), await store.staged())
    cfg = render_config(eff, decrypt=lambda b: f.decrypt(b.encode()).decode())
    return {"config": redact_rendered(cfg)}
```
(`make_reloader` import: reuse the existing reloader factory — import it or define the seam identically to the old config routes.)

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): POST /api/apply + /api/discard + GET /api/config/rendered`.

---

## Task 4: GET/PUT /api/config/passthrough (TDD)

**Files:** Modify `config_v3_routes.py`, `test_config_v3_routes.py`.

- [ ] **Step 1: failing tests:**
```python
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
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement:**
```python
import yaml as _yaml
@router.get("/config/passthrough", dependencies=[Depends(login_required)])
async def get_passthrough():
    store = make_config_store()
    eff = {(i["kind"], i["name"]): i for i in effective(await store.applied(), await store.staged())}
    it = eff.get(("passthrough", "_"))
    data = (it["data"] if it and it.get("flag") != "deleted" else {}) or {}
    return {"data": data, "yaml": _yaml.safe_dump(data, sort_keys=False) if data else ""}

@router.put("/config/passthrough", dependencies=[Depends(login_required)])
async def put_passthrough(body: dict = Body(...)):
    raw = body.get("yaml", "")
    try:
        data = _yaml.safe_load(raw) or {}
        if not isinstance(data, dict): raise ValueError("passthrough must be a YAML mapping")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid passthrough YAML: {e}")
    await make_config_store().stage("passthrough", "_", data)
    return await pending_status(make_config_store())
```

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): GET/PUT /api/config/passthrough (raw advanced config)`.

---

## Task 5: retire the v2 config + credential routes

**Files:** Modify `ui/app/main.py`; Delete `ui/app/routes/config_routes.py`, `ui/app/routes/credentials_routes.py`, `ui/tests/test_config_routes.py`, `ui/tests/test_credentials_routes.py`.

- [ ] **Step 1:** in `main.py`, remove the `include_router` for `config_routes` and `credentials_routes`; keep auth/keys/usage/housekeeping/catalog/models routers. Ensure no remaining import references them.
- [ ] **Step 2:** `git rm ui/app/routes/config_routes.py ui/app/routes/credentials_routes.py ui/tests/test_config_routes.py ui/tests/test_credentials_routes.py`.
- [ ] **Step 3:** grep for stragglers: `grep -rn "config_routes\|credentials_routes\|/api/credentials\|api/apply/status\|/api/cache/info" ui/app` → fix any import; the v2 `app.apply` + `app.safe_apply`-style helpers used only by the removed routes can stay (dead) — leave for a v3.3 cleanup to keep this PR focused (note them).
- [ ] **Step 4:** run full suite — it should pass with the v2 route tests removed and the v3 tests present. Fix any test that imported the deleted modules.
- [ ] **Step 5: commit** `refactor(ui): retire v2 config + credential routes (superseded by /api/config/* item API)`.

---

## Task 6: real-stack integration verification

- [ ] **Step 1:** local-build override; `docker compose up -d --build --wait`; login.
- [ ] **Step 2:** `GET /api/config/state` → bootstrap-seeded items (router/litellm/general settings), pending false.
- [ ] **Step 3:** `PUT /api/config/item` (router_setting routing_strategy=least-busy) → pending true; `GET /api/config/state` shows it `changed`. `PUT` a credential (kind=credential, api_key) → staged; `GET /api/config/state` shows it redacted (`api_key:"***"`); `GET /api/config/rendered` shows it `***` too.
- [ ] **Step 4:** `POST /api/apply` → 200 `{applied:true, servant:"healthy"}`; `config.yaml` rendered (routing least-busy + credential_list literal at 0600); `GET /api/config/state` pending false (folded). `/v1/models` healthy.
- [ ] **Step 5:** `PUT /api/config/item` (a model using the credential) → `POST /api/apply` → model in `/v1/models`. `DELETE /api/config/item/credential/<name>` → `GET /api/config/state` shows it struck (`deleted` flag) while pending; `POST /api/discard` → restores (flag gone). Passthrough: `PUT /api/config/passthrough` a key → `GET /api/config/rendered` shows it merged; apply.
- [ ] **Step 6:** Tear down; restore config; `git status` clean.

## Self-Review
- **Spec coverage:** state (T1) ✓; item PUT/DELETE incl. credential-encrypt + redact (T2) ✓; apply (commit-at-write mapping) + discard + rendered (T3) ✓; passthrough (T4) ✓; retire v2 routes (T5) ✓; integration incl. delete-strikethrough + discard-restore + passthrough (T6) ✓.
- **Placeholders:** `make_reloader` reuse noted (import the existing factory); credential-update-without-key documented as out-of-scope (write-only keys).
- **Type consistency:** seams `make_config_store`/`make_reloader`/`_fernet`; `effective`/`render_config`/`redact_rendered`/`pending_status`/`apply_config`/`ApplyError` (v3.1) used consistently; item shape `{kind,name,data,flag}`; routes return `pending_status` shape `{pending,count}`.

## Follow-on
v3.3 — frontend rewiring (every config screen → `/api/config/*`, flag rendering, Apply/Discard bar, passthrough editor, rendered preview) + dead-code cleanup (v1/v2 `apply.py`, file-diff baseline helpers). Written after this API is built + verified.
