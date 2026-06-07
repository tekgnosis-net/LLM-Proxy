# LLM-Proxy Admin UI — v2.2 (Provider Keys + Models v2) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD. Frontend = build + real-stack verification. Steps use `- [ ]`. **Builds on v2.1.**

**Goal:** A **UI-owned provider-key vault** (typed in the UI, encrypted in an app DB table) that is **materialized into `config.yaml`** so it survives the restart-based Apply; plus **Models v2** — pick a saved credential, choose mode/endpoint, set custom costs, **Test connection before saving**, see **per-model health**.

**Why this shape (spike finding):** In config-only mode LiteLLM's own `POST /credentials` writes the DB but does **NOT** reload it on restart — so a pure LiteLLM-DB vault vanishes on every Apply (= restart). Therefore the **UI owns** the vault (`ui_credentials`, encrypted) and renders it into `config.yaml`'s `credential_list` (which LiteLLM *does* reload). Consequence: **`config.yaml` becomes secret-bearing** → `0600`, gitignored (live) with a committed secret-free `.example`, the no-literal-secrets guardrail exempts `credential_list`, and export redacts.

**Tech Stack:** FastAPI, asyncpg, httpx, **`cryptography`** (Fernet) — new dep; Svelte 5.

**Spec:** [`../specs/2026-06-07-llm-proxy-ui-v2-design.md`](../specs/2026-06-07-llm-proxy-ui-v2-design.md) (§ Phase v2.2).

---

## File Structure
```
ui/pyproject.toml                 # + cryptography
ui/app/config_store.py            # MODIFY: write_config 0600; guardrail exempts credential_list; seed-from-example
ui/app/routes/config_routes.py    # MODIFY: GET redacts credential_list; PUT injects vault; export redacts
ui/app/credentials_store.py       # CREATE: ui_credentials table (Fernet) + CRUD + materialize
ui/app/routes/credentials_routes.py # CREATE: /api/credentials (create/list/delete) → re-materialize + stage
ui/app/routes/models_routes.py    # CREATE: /api/models/test, /api/models/health
ui/app/settings.py                # MODIFY: credentials_key (derive from session_secret if empty)
ui/app/main.py                    # MODIFY: include new routers
config/config.yaml.example        # CREATE (committed secret-free bootstrap); config/config.yaml gitignored
.gitignore                        # MODIFY: config/config.yaml
ui/tests/test_credentials.py      # CREATE (Fernet roundtrip + materialize, pure)
ui/tests/test_credentials_routes.py / test_models_routes.py / test_config_routes.py  # CREATE/MODIFY
ui/frontend/src/routes/ProviderKeys.svelte  # CREATE
ui/frontend/src/routes/Models.svelte         # MODIFY
ui/frontend/src/lib/api.js / App.svelte      # MODIFY
```

---

## Task 1 — SPIKE (DONE)

Finding recorded: LiteLLM does NOT reload DB credentials on restart in config-only mode (DB row persists, `/credentials` returns empty after restart). Decision: UI-owned vault materialized into `config.yaml`. No code in this task; the finding drives Tasks 2–4.

---

## Task 2 — config.yaml secret-bearing foundation

**Files:** `ui/app/config_store.py`, `ui/app/routes/config_routes.py`, `.gitignore`, `config/config.yaml.example`, `ui/tests/test_config_store.py`, `ui/tests/test_config_routes.py`.

- [ ] **Step 1: failing tests** (append to `test_config_store.py`):
```python
import os, stat
from app.config_store import write_config, validate_config, ConfigError

def test_write_config_is_0600(tmp_path):
    p = str(tmp_path / "config.yaml")
    write_config(p, {"model_list": []})
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600

def test_credential_list_literals_are_allowed(tmp_path):
    # the no-literal-secrets guardrail must EXEMPT credential_list (UI materializes literals there)
    cfg = validate_config({"credential_list": [
        {"credential_name": "openai", "credential_values": {"api_key": "sk-REAL"}, "credential_info": {}}]})
    assert cfg is not None

def test_model_list_literal_secret_still_rejected(tmp_path):
    with pytest.raises(ConfigError):
        validate_config({"model_list": [{"model_name": "x", "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-LITERAL"}}]})
```

- [ ] **Step 2: run red** → FAIL (0600 not set; credential_list literal rejected by current guardrail).

- [ ] **Step 3: implement**
  - In `config_store.py` `write_config`: change `os.chmod(tmp, 0o644)` → `os.chmod(tmp, 0o600)` (config now holds secrets). Update the comment.
  - Add `CredentialListModel`-tolerance + exempt credential_list in `_check_no_literal_secrets`: skip recursion into the `credential_list` subtree:
    ```python
    def _check_no_literal_secrets(node, *, in_credential_list=False) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "credential_list":
                    continue  # UI materializes literal credential values here by design
                if k in SECRET_FIELDS and isinstance(v, str) and v and not v.startswith("os.environ/"):
                    raise ConfigError(f"secret field {k!r} must be an os.environ/<VAR> reference, not a literal value")
                _check_no_literal_secrets(v)
        elif isinstance(node, list):
            for item in node:
                _check_no_literal_secrets(item)
    ```
    Also add `credential_list` to `ProxyConfig` as an optional passthrough field (it already has `extra="allow"`, so it round-trips; confirm `model_dump` keeps it).
  - Add `seed_config_from_example(config_path)`: if `config_path` missing and `<dir>/config.yaml.example` exists, copy example→config_path (0600). Call it in `main.create_app()` (or lifespan) before first use.
  - In `config_routes.py`: `GET /api/config` and `GET /api/config/export` must **redact** `credential_list` values:
    ```python
    def _redact(cfg: dict) -> dict:
        cl = cfg.get("credential_list")
        if isinstance(cl, list):
            cfg = {**cfg, "credential_list": [{**c, "credential_values": {k: "***" for k in (c.get("credential_values") or {})}} for c in cl]}
        return cfg
    ```
    Apply `_redact` to the GET /api/config response and the export text (parse→redact→dump for export).

- [ ] **Step 4: git** — stop tracking the live config + commit the example:
```bash
cp config/config.yaml config/config.yaml.example   # current bootstrap is secret-free (os.environ/ refs)
git rm --cached config/config.yaml
printf '\nconfig/config.yaml\n' >> .gitignore
git add config/config.yaml.example .gitignore
```
(Leave the live `config/config.yaml` on disk — now untracked.)

- [ ] **Step 5: pyproject** — add `"cryptography>=43"` to dependencies; `cd ui && .venv/bin/pip install cryptography`.

- [ ] **Step 6: green + full suite** (`cd ui && .venv/bin/python -m pytest -q`). **Step 7: commit** `feat(ui): config.yaml secret-bearing (0600, gitignored, .example, credential_list exempt + redacted)`.

---

## Task 3 — credentials_store (TDD)

**Files:** Create `ui/app/credentials_store.py`, `ui/tests/test_credentials.py`; Modify `ui/app/settings.py`.

- [ ] **Step 1: settings** add:
```python
    credentials_key: str = ""   # Fernet key (urlsafe-b64, 32 bytes); empty → derived from session_secret
```

- [ ] **Step 2: failing tests** (`ui/tests/test_credentials.py`) — pure crypto + materialize:
```python
from app.credentials_store import fernet_from_secret, materialize_credentials

def test_fernet_roundtrip():
    f = fernet_from_secret("test-secret")
    tok = f.encrypt(b"sk-REAL")
    assert f.decrypt(tok) == b"sk-REAL"

def test_materialize_injects_credential_list():
    cfg = {"model_list": [{"model_name": "x", "litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "openai"}}]}
    decrypted = [{"credential_name": "openai", "provider": "openai", "api_key": "sk-REAL"}]
    out = materialize_credentials(cfg, decrypted)
    cl = out["credential_list"]
    assert cl[0]["credential_name"] == "openai"
    assert cl[0]["credential_values"]["api_key"] == "sk-REAL"
    assert cl[0]["credential_info"]["provider"] == "openai"

def test_materialize_empty_removes_credential_list():
    out = materialize_credentials({"credential_list": [{"credential_name": "old"}], "model_list": []}, [])
    assert not out.get("credential_list")
```

- [ ] **Step 3: run red** → FAIL. **Step 4: implement `ui/app/credentials_store.py`:**
```python
from __future__ import annotations
import base64, hashlib
import asyncpg
from cryptography.fernet import Fernet
from typing import Any, Optional


def fernet_from_secret(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256((secret or "change-me").encode()).digest())
    return Fernet(key)


def materialize_credentials(config: dict, decrypted: list[dict]) -> dict:
    """Pure: inject a credential_list (literal values) built from the decrypted vault entries.
    Empty vault → drop credential_list. Does not mutate the input."""
    out = {k: v for k, v in config.items() if k != "credential_list"}
    if decrypted:
        out["credential_list"] = [
            {"credential_name": c["credential_name"],
             "credential_values": {"api_key": c["api_key"]},
             "credential_info": {"provider": c.get("provider")}}
            for c in decrypted
        ]
    return out


class CredentialsStore:
    def __init__(self, dsn: str, fernet: Fernet):
        self._dsn = dsn; self._f = fernet

    async def _conn(self): return await asyncpg.connect(self._dsn)

    async def ensure_schema(self, conn) -> None:
        await conn.execute('''CREATE TABLE IF NOT EXISTS ui_credentials (
            credential_name text PRIMARY KEY, provider text,
            value_encrypted text NOT NULL, created_at timestamptz default now())''')

    async def create(self, name: str, provider: str, api_key: str) -> None:
        enc = self._f.encrypt(api_key.encode()).decode()
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            await conn.execute('''INSERT INTO ui_credentials(credential_name,provider,value_encrypted)
                VALUES($1,$2,$3) ON CONFLICT(credential_name) DO UPDATE SET provider=$2,value_encrypted=$3''',
                name, provider, enc)
        finally: await conn.close()

    async def list_masked(self) -> list[dict[str, Any]]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            rows = await conn.fetch("SELECT credential_name, provider, created_at FROM ui_credentials ORDER BY credential_name")
            return [{"credential_name": r["credential_name"], "provider": r["provider"]} for r in rows]
        finally: await conn.close()

    async def list_decrypted(self) -> list[dict[str, Any]]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            rows = await conn.fetch("SELECT credential_name, provider, value_encrypted FROM ui_credentials ORDER BY credential_name")
            return [{"credential_name": r["credential_name"], "provider": r["provider"],
                     "api_key": self._f.decrypt(r["value_encrypted"].encode()).decode()} for r in rows]
        finally: await conn.close()

    async def delete(self, name: str) -> None:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            await conn.execute("DELETE FROM ui_credentials WHERE credential_name=$1", name)
        finally: await conn.close()
```

- [ ] **Step 5: green + full suite.** **Step 6: commit** `feat(ui): credentials_store (encrypted ui vault + materialize)`.

---

## Task 4 — /api/credentials routes + materialize on save (TDD)

**Files:** Create `ui/app/routes/credentials_routes.py`; Modify `ui/app/routes/config_routes.py`, `ui/app/main.py`; Create `ui/tests/test_credentials_routes.py`.

- [ ] **Step 1: failing tests** (`test_credentials_routes.py`, fake store via `make_credentials_store` seam):
```python
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
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement.**
  - A shared helper (in `credentials_routes.py`) to re-materialize after a vault change:
```python
from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.credentials_store import CredentialsStore, materialize_credentials, fernet_from_secret
from app.config_store import load_config, write_config, pending_status, seed_baseline_if_missing
from app.settings import get_settings

router = APIRouter(prefix="/api")

def make_credentials_store() -> CredentialsStore:
    s = get_settings()
    if not s.database_url: raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return CredentialsStore(s.database_url, fernet_from_secret(s.credentials_key or s.session_secret))

async def _rematerialize_and_stage():
    """Re-render the vault into config.yaml (staged) so a later Apply picks it up."""
    s = get_settings()
    store = make_credentials_store()
    decrypted = await store.list_decrypted()
    current = load_config(s.config_path).model_dump(exclude_none=True)
    seed_baseline_if_missing(s.config_path)
    write_config(s.config_path, materialize_credentials(current, decrypted))
    return pending_status(s.config_path)

@router.get("/credentials", dependencies=[Depends(login_required)])
async def list_credentials():
    try: return await make_credentials_store().list_masked()
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"credentials error: {e}")

@router.post("/credentials", dependencies=[Depends(login_required)])
async def create_credential(body: dict = Body(...)):
    name, prov, key = body.get("credential_name"), body.get("provider"), body.get("api_key")
    if not name or not key: raise HTTPException(status_code=422, detail="credential_name and api_key required")
    try:
        await make_credentials_store().create(name, prov, key)
        return {"ok": True, **(await _rematerialize_and_stage())}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"credentials error: {e}")

@router.delete("/credentials/{name}", dependencies=[Depends(login_required)])
async def delete_credential(name: str):
    try:
        await make_credentials_store().delete(name)
        return {"ok": True, **(await _rematerialize_and_stage())}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=502, detail=f"credentials error: {e}")
```
  - In `config_routes.py`: apply `_redact` (from Task 2) to GET /api/config + export. AND change `PUT /api/config` to **inject** the vault before write so a model save doesn't drop credential_list:
```python
# put_config: after validating-by-writing, ensure credential_list reflects the vault.
# Simplest: ignore any credential_list the frontend sent (it's redacted anyway), then
# re-materialize from the vault. Implement by calling the credentials_routes helper:
from app.routes.credentials_routes import make_credentials_store
...
@router.put("/config", dependencies=[Depends(login_required)])
async def put_config(raw: dict = Body(...)):
    s = get_settings()
    raw = {k: v for k, v in raw.items() if k != "credential_list"}   # never trust client credential_list
    try:
        decrypted = await make_credentials_store().list_decrypted()
    except Exception:
        decrypted = []
    from app.credentials_store import materialize_credentials
    try:
        write_config(s.config_path, materialize_credentials(raw, decrypted))
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, **pending_status(s.config_path)}
```
(PUT becomes `async` to await the vault. Keep `make_reloader` for apply.)
  - Wire `credentials_routes.router` into `main.py`.

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): /api/credentials vault + materialize into config (GET redacts, PUT injects)`.

---

## Task 5 — /api/models/test + /api/models/health (TDD)

**Files:** Create `ui/app/routes/models_routes.py`; Modify `ui/app/litellm_client.py`, `ui/app/main.py`; Create `ui/tests/test_models_routes.py`.

(Unchanged from the original v2.2 design — see below.)

- [ ] **Step 1: failing tests** (`test_models_routes.py`, fake via `make_models_client` seam):
```python
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
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement.** Add to `litellm_client.py`:
```python
    async def test_connection(self, litellm_params: dict, mode: str = "chat") -> dict:
        async with self._client() as c:
            r = await c.post(f"{self._base}/health/test_connection", json={"litellm_params": litellm_params, "mode": mode})
            return {"status": "success", "result": r.json()} if r.status_code < 400 else {"status": "error", "result": r.text}
    async def health_all(self) -> dict:
        async with self._client() as c:
            r = await c.get(f"{self._base}/health"); r.raise_for_status(); return r.json()
```
`models_routes.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.litellm_client import LitellmClient
from app.settings import get_settings
router = APIRouter(prefix="/api")
def make_models_client() -> LitellmClient:
    s = get_settings(); return LitellmClient(s.litellm_base_url, s.litellm_master_key)
@router.post("/models/test", dependencies=[Depends(login_required)])
async def test_model(body: dict = Body(...)):
    lp = body.get("litellm_params") or {}
    if not lp.get("model"): raise HTTPException(status_code=422, detail="litellm_params.model required")
    try: return await make_models_client().test_connection(lp, body.get("mode", "chat"))
    except Exception as e: raise HTTPException(status_code=502, detail=f"test failed: {e}")
@router.get("/models/health", dependencies=[Depends(login_required)])
async def models_health():
    try: return await make_models_client().health_all()
    except Exception as e: raise HTTPException(status_code=502, detail=f"health error: {e}")
```
Wire into `main.py`.

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): /api/models/test (pre-save) + /api/models/health (cached)`.

---

## Task 6 — Provider Keys screen + nav

**Files:** Modify `ui/frontend/src/lib/api.js`, `ui/frontend/src/App.svelte`; Create `ui/frontend/src/routes/ProviderKeys.svelte`.

- [ ] **Step 1: api.js:** `credentials: () => req('/api/credentials')`, `createCredential: (b)=>req('/api/credentials',{method:'POST',body:JSON.stringify(b)})`, `deleteCredential: (n)=>req(\`/api/credentials/${encodeURIComponent(n)}\`,{method:'DELETE'})`.
- [ ] **Step 2: `ProviderKeys.svelte`** — type a key (encrypted server-side), list (masked), delete; note that saving a key stages a config change (Apply to activate). Takes the shared `store` to refresh pending after create/delete (the route returns pending). Full component:
```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { PROVIDERS } from '../lib/providers.js'
  let { store } = $props()
  let creds = $state([]), err = $state(''), busy = $state(false), showAdd = $state(false)
  let form = $state({ credential_name:'', provider:'openai', api_key:'' })
  async function load(){ try{ creds = await api.credentials() }catch(e){ err=e.message } }
  onMount(load)
  async function add(){ busy=true; err=''
    try{ const r=await api.createCredential({credential_name:form.credential_name,provider:form.provider,api_key:form.api_key});
      if (store) { store.pending = true } ; form={credential_name:'',provider:'openai',api_key:''}; showAdd=false; await load(); store?.refreshPending?.() }
    catch(e){ err=e.message } finally{ busy=false } }
  async function del(n){ if(!confirm(`Delete "${n}"? Models using it will fail after the next Apply.`))return; busy=true
    try{ await api.deleteCredential(n); await load(); store?.refreshPending?.() }catch(e){ err=e.message } finally{ busy=false } }
</script>
<div class="page"><header><h1>Provider Keys</h1><button class="primary" onclick={()=>showAdd=!showAdd}>＋ Add key</button></header>
  {#if err}<div class="banner err">{err}</div>{/if}
  <p class="hint">Keys are encrypted at rest in the app database and written into <code>config.yaml</code> on Apply. Values are never shown again. Saving or deleting a key stages a change — click <strong>Apply</strong> to activate.</p>
  {#if showAdd}<div class="card add">
    <label>Name <input bind:value={form.credential_name} placeholder="e.g. openai_prod" /></label>
    <label>Provider <select bind:value={form.provider}>{#each PROVIDERS as p}<option value={p.id}>{p.label}</option>{/each}</select></label>
    <label>API key <input type="password" bind:value={form.api_key} placeholder="sk-…" /></label>
    <div class="row"><button class="primary" onclick={add} disabled={busy||!form.credential_name||!form.api_key}>Save key</button><button onclick={()=>showAdd=false}>Cancel</button></div>
  </div>{/if}
  <div class="card">{#if creds.length===0}<p class="empty">No provider keys yet.</p>{:else}
    <table><thead><tr><th>Name</th><th>Provider</th><th></th></tr></thead><tbody>
      {#each creds as k}<tr><td>{k.credential_name}</td><td>{k.provider||'—'}</td>
        <td><button class="danger" onclick={()=>del(k.credential_name)} disabled={busy}>Delete</button></td></tr>{/each}
    </tbody></table>{/if}</div>
</div>
<style>
  .page{padding:24px 30px;max-width:760px}header{display:flex;justify-content:space-between;align-items:center}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;margin-top:14px;background:var(--card,#fff)}
  .card.add{display:flex;flex-direction:column;gap:10px;max-width:420px}
  label{display:flex;flex-direction:column;font-size:13px;gap:4px;color:var(--muted,#3a3a3c)}
  input,select{padding:8px;border:1px solid var(--border,#ccc);border-radius:8px;font:inherit}
  table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--border,rgba(0,0,0,.06));font-size:14px}
  .row{display:flex;gap:8px}button{padding:8px 12px;border:1px solid var(--border,#ccc);border-radius:8px;background:var(--card,#fff);font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}button.danger{color:#ff3b30;border-color:#ffd0cc}button:disabled{opacity:.5}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;font-size:13px}.hint{font-size:12px;color:var(--muted,#6e6e73)}.empty{color:var(--muted,#6e6e73)}
</style>
```
- [ ] **Step 3: App.svelte** — `import ProviderKeys`; Access nav group: `<button class="nav" class:active={screen==='providerkeys'} onclick={()=>screen='providerkeys'}>🗝 Provider Keys</button>`; render `{:else if screen==='providerkeys'}<ProviderKeys {store} />`.
- [ ] **Step 4: build** + commit `feat(ui): Provider Keys screen (UI-owned encrypted vault)`.

---

## Task 7 — Models v2 (credential / mode / costs / test / health)

**Files:** Modify `ui/frontend/src/routes/Models.svelte`, `ui/frontend/src/lib/api.js`.

- [ ] **Step 1: api.js:** `testModel:(b)=>req('/api/models/test',{method:'POST',body:JSON.stringify(b)})`, `modelsHealth:()=>req('/api/models/health')`.
- [ ] **Step 2: Models.svelte** (extend; READ the existing file first to match patterns):
  - Load `credentials` (`api.credentials()`) for the dropdown; load `modelsHealth()` → map model_name→healthy.
  - Form gains: `credential` (''|credential_name), `mode` ('chat'…), `input_cost`, `output_cost`.
  - `buildLitellmParams`: if `credential` → add `litellm_credential_name: credential` (no api_key); else keep the existing env-var path. Add `input_cost_per_token`/`output_cost_per_token` (Number) when set; put `mode` into `model_info:{mode}`.
  - **Test** button: `await api.testModel({litellm_params: builtParams, mode})` → inline success/error. Works before Save.
  - **Health** column in the table: green/red dot per `m.model_name` from the health map.
  - Save stages (per v2.1); the global Apply bar applies.
- [ ] **Step 3: markup** add to the add-model card:
```svelte
  <label>Credential <select bind:value={form.credential}>
    <option value="">— env var / none —</option>
    {#each credentials as c}<option value={c.credential_name}>{c.credential_name}</option>{/each}</select></label>
  <label>Mode <select bind:value={form.mode}>{#each ['chat','embedding','completion','image_generation','audio_transcription','rerank','moderations'] as m}<option value={m}>{m}</option>{/each}</select></label>
  <label>Input cost/token <input type="number" step="1e-9" min="0" bind:value={form.input_cost} placeholder="auto (v2.3)" /></label>
  <label>Output cost/token <input type="number" step="1e-9" min="0" bind:value={form.output_cost} placeholder="auto (v2.3)" /></label>
  <div class="row"><button onclick={testConn} disabled={busy||!form.modelName||!form.modelId}>Test connection</button>
    <button class="primary" onclick={addModel} disabled={store.applying||!form.modelName||!form.modelId}>Save</button>
    <button onclick={resetForm}>Cancel</button></div>
  {#if testResult}<div class="banner {testResult.ok?'ok':'err'}">{testResult.msg}</div>{/if}
```
- [ ] **Step 4: build** + commit `feat(ui): Models v2 — credential/mode/costs/test-connection/health`.

---

## Task 8 — enable cached background health checks

- [ ] **Step 1:** ensure `general_settings.background_health_checks: true` + `health_check_interval: 300` are present in `config/config.yaml.example` (and seeded). Add a `test_config_store.py` assertion that these keys round-trip (extra="allow"). Commit `feat(ui): enable cached background health checks`.

---

## Task 9 — real-stack integration verification

- [ ] **Step 1:** local-build override (as in v2.1 T9) → `docker compose up -d --build --wait`; log in.
- [ ] **Step 2:** **Provider Keys** → add `openai_test` (provider openai, key sk-fake) → masked in list; `config/config.yaml` now has `credential_list` with the literal (0600); `GET /api/config` shows it **redacted** (`***`). Apply bar pending.
- [ ] **Step 3:** **Apply** → restart → `GET /credentials` style still works; **critically**: after the restart the credential is usable — add a Model referencing `openai_test`, Save, Apply, and confirm the model appears in `/v1/models` (credential resolved from `config.yaml`'s `credential_list`, surviving the restart — the whole point).
- [ ] **Step 4:** **Models** Test connection (fake key → expect error result, but endpoint round-trips); health column renders. Delete the credential → re-materialized (config_list emptied) → Apply.
- [ ] **Step 5:** Tear down; `rm docker-compose.override.yml`; restore: `rm -f config/config.yaml && cp config/config.yaml.example config/config.yaml` (or git checkout the example); `rm -f config/.applied.yaml config/config.yaml.bak.*`; confirm `git status` clean.

## Self-Review
- **Spec coverage:** UI-owned encrypted vault (T3) ✓; materialize into config.yaml + survives restart (T3,T4, verified T9) ✓; config.yaml secret-bearing 0600/gitignored/.example/redacted (T2) ✓; guardrail exempts credential_list but rejects model literals (T2) ✓; Models credential/mode/cost/test/health (T5,T7,T8) ✓; GET redacts, PUT injects (T4) ✓.
- **Placeholders:** Models.svelte (T7) extends the existing file with exact fields/markup; ProviderKeys/credentials_store/routes have full code.
- **Type consistency:** `fernet_from_secret`/`materialize_credentials`/`CredentialsStore.{create,list_masked,list_decrypted,delete}`, `make_credentials_store` seam + `_rematerialize_and_stage`, `_redact`, `make_models_client`/`LitellmClient.{test_connection,health_all}`, `api.{credentials,createCredential,deleteCredential,testModel,modelsHealth}` consistent. Table `ui_credentials`.

## Notes / risks
- **Encryption key:** Fernet key derives from `credentials_key` (or `session_secret`). Rotating it makes stored keys undecryptable (re-enter) — document alongside the `LITELLM_SALT_KEY` caveat. A dedicated `CREDENTIALS_KEY` in `.env` decouples it from session rotation (optional).
- **config.yaml ownership:** now `0600` root-owned (secrets) — hand-edit via the UI or `sudo`; the committed `.example` is the shareable template.
- DB CRUD in `credentials_store` is integration-verified (T9); unit tests cover the pure crypto + materialize + the routes (fake store).
