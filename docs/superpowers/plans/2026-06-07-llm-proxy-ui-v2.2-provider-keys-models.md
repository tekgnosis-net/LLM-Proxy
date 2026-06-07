# LLM-Proxy Admin UI — v2.2 (Provider Keys + Models v2) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD. Frontend = build + real-stack verification. Steps use `- [ ]`. **Builds on v2.1** (staged save + global Apply).

**Goal:** A DB-backed **Provider Keys** vault (typed in the UI, like LiteLLM's own), and **Models v2** — pick a saved credential, choose mode/endpoint, set custom costs, **Test connection before saving**, and see **per-model health**.

**Architecture:** New `credentials_client` + `/api/credentials` proxy LiteLLM's credentials API (encrypted in Postgres via `LITELLM_SALT_KEY`). `/api/models/test` → `POST /health/test_connection` (candidate, unsaved). `/api/models/health` → cached `GET /health` (enable `background_health_checks`). Models reference a credential via `litellm_credential_name`.

**Tech Stack:** FastAPI, httpx, Svelte 5. No new deps.

**Spec:** [`../specs/2026-06-07-llm-proxy-ui-v2-design.md`](../specs/2026-06-07-llm-proxy-ui-v2-design.md) (§ Phase v2.2). **Verified:** credentials endpoints `POST/GET/DELETE /credentials`; writes need a DB (we have Postgres); `POST /health/test_connection` tests an unsaved candidate but rejects request-supplied `os.environ/` refs and needs a DB.

---

## File Structure
```
ui/app/credentials_client.py        # CREATE
ui/app/routes/credentials_routes.py # CREATE: /api/credentials
ui/app/routes/models_routes.py      # CREATE: /api/models/test, /api/models/health
ui/app/main.py                      # MODIFY: include the two routers
ui/tests/test_credentials_client.py # CREATE
ui/tests/test_credentials_routes.py # CREATE
ui/tests/test_models_routes.py      # CREATE
ui/frontend/src/lib/api.js          # MODIFY: credentials + models test/health helpers
ui/frontend/src/routes/ProviderKeys.svelte  # CREATE
ui/frontend/src/routes/Models.svelte         # MODIFY: credential/mode/cost/test/health
ui/frontend/src/App.svelte          # MODIFY: ProviderKeys nav
config/config.yaml                  # (runtime) enable background_health_checks via UI/general_settings
```

---

## Task 1: SPIKE — do DB credentials survive a restart in config-only mode?

**Files:** none (investigation; record the finding in `credentials_client.py`'s header comment when built).

- [ ] **Step 1:** `docker compose up -d --wait`; log in; read master key (`MK`) without printing.
- [ ] **Step 2:** Create a credential via the proxy:
```bash
curl -s -H "Authorization: Bearer $MK" -H 'Content-Type: application/json' -X POST http://localhost:4000/credentials \
  -d '{"credential_name":"spike_cred","credential_values":{"api_key":"sk-fake-spike"},"credential_info":{}}'
curl -s -H "Authorization: Bearer $MK" http://localhost:4000/credentials | grep spike_cred   # present now
```
- [ ] **Step 3:** Restart the proxy (config-only: `store_model_in_db:false`) and re-check:
```bash
docker compose restart litellm && docker compose up -d --wait
curl -s -H "Authorization: Bearer $MK" http://localhost:4000/credentials | grep spike_cred || echo "GONE after restart"
```
- [ ] **Step 4: Decide + record the finding:**
  - **If `spike_cred` survives** → DB credentials reload on restart; build the vault straight against the credentials API (Tasks 2–3 as written).
  - **If it's GONE** → DB credentials don't reload in config-only mode. Fallback: on create, the UI ALSO writes a `credential_list` entry into `config.yaml` (name + `credential_values: {api_key: os.environ/<VAR>}`) so it survives restart, and the secret value lives in the DB credential (for resolution) AND/or `.env`. Adjust Task 3's create handler to mirror into config.yaml (staged → applied via the v2.1 bar). Document the chosen path at the top of `credentials_client.py`.
  - Clean up: delete `spike_cred`; `docker compose down`; restore `config/config.yaml`.

---

## Task 2: credentials_client (TDD)

**Files:** Create `ui/app/credentials_client.py`, `ui/tests/test_credentials_client.py`.

- [ ] **Step 1: failing tests:**
```python
import httpx, pytest
from app.credentials_client import CredentialsClient


def _c(handler):
    return CredentialsClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_credentials_masked_passthrough():
    def h(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        return httpx.Response(200, json={"credentials": [{"credential_name": "openai_prod", "credential_info": {}}]})
    out = await _c(h).list_credentials()
    assert out[0]["credential_name"] == "openai_prod"


@pytest.mark.asyncio
async def test_create_credential_posts_body():
    seen = {}
    def h(req):
        import json; seen["b"] = json.loads(req.content); return httpx.Response(200, json={"credential_name": "x"})
    await _c(h).create_credential("x", {"api_key": "sk-real"}, {"provider": "openai"})
    assert seen["b"]["credential_name"] == "x" and seen["b"]["credential_values"]["api_key"] == "sk-real"


@pytest.mark.asyncio
async def test_delete_credential():
    def h(req):
        assert req.method == "DELETE" and req.url.path.endswith("/credentials/x")
        return httpx.Response(200, json={"deleted": "x"})
    assert (await _c(h).delete_credential("x"))["deleted"] == "x"
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement `ui/app/credentials_client.py`:**
```python
from __future__ import annotations
# NOTE (v2.2 spike): record here whether DB credentials reload across restart in
# config-only mode, and the chosen approach (pure DB vs. config_list mirror).
import httpx
from typing import Any, Optional


class CredentialsClient:
    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/"); self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self): return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def list_credentials(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/credentials"); r.raise_for_status()
            d = r.json(); creds = d.get("credentials", d) if isinstance(d, dict) else d
            return [k for k in creds if isinstance(k, dict)]

    async def create_credential(self, name: str, values: dict, info: dict | None = None) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/credentials",
                             json={"credential_name": name, "credential_values": values, "credential_info": info or {}})
            r.raise_for_status(); return r.json()

    async def delete_credential(self, name: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.delete(f"{self._base}/credentials/{name}"); r.raise_for_status(); return r.json()
```

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): credentials_client (litellm credential vault)`.

---

## Task 3: /api/credentials routes (TDD)

**Files:** Create `ui/app/routes/credentials_routes.py`, `ui/tests/test_credentials_routes.py`; Modify `ui/app/main.py`.

- [ ] **Step 1: failing tests** (fake client via `make_credentials_client` seam):
```python
import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password

def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s", CONFIG_PATH=str(tmp_path/"c.yaml"))
    (tmp_path/"c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.credentials_routes as cr
    cr.make_credentials_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password":"pw"}); return c

class FakeCred:
    def __init__(self): self.created=None; self.deleted=None
    async def list_credentials(self): return [{"credential_name":"openai_prod","credential_info":{"provider":"openai"}}]
    async def create_credential(self,n,v,i=None): self.created=(n,v,i); return {"credential_name":n}
    async def delete_credential(self,n): self.deleted=n; return {"deleted":n}

def test_requires_login(tmp_path):
    c=_client(tmp_path,FakeCred()); c.cookies.clear(); assert c.get("/api/credentials").status_code==401
def test_list(tmp_path):
    r=_client(tmp_path,FakeCred()).get("/api/credentials"); assert r.json()[0]["credential_name"]=="openai_prod"
    assert "credential_values" not in r.json()[0]   # never returns secret values
def test_create(tmp_path):
    f=FakeCred(); c=_client(tmp_path,f)
    r=c.post("/api/credentials", json={"credential_name":"x","provider":"openai","api_key":"sk-real"})
    assert r.status_code==200 and f.created[0]=="x" and f.created[1]["api_key"]=="sk-real"
def test_delete(tmp_path):
    f=FakeCred(); c=_client(tmp_path,f); assert c.request("DELETE","/api/credentials/x").status_code==200 and f.deleted=="x"
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement `ui/app/routes/credentials_routes.py`:**
```python
from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.credentials_client import CredentialsClient
from app.settings import get_settings

router = APIRouter(prefix="/api")

def make_credentials_client() -> CredentialsClient:
    s = get_settings()
    return CredentialsClient(s.litellm_base_url, s.litellm_master_key)

@router.get("/credentials", dependencies=[Depends(login_required)])
async def list_credentials():
    try: return await make_credentials_client().list_credentials()
    except Exception as e: raise HTTPException(status_code=502, detail=f"proxy credentials error: {e}")

@router.post("/credentials", dependencies=[Depends(login_required)])
async def create_credential(body: dict = Body(...)):
    name = body.get("credential_name")
    if not name: raise HTTPException(status_code=422, detail="credential_name required")
    values = {k: v for k, v in body.items() if k not in ("credential_name", "provider") and v not in (None, "")}
    info = {"provider": body.get("provider")} if body.get("provider") else {}
    try: return await make_credentials_client().create_credential(name, values, info)
    except Exception as e: raise HTTPException(status_code=502, detail=f"proxy credentials error: {e}")

@router.delete("/credentials/{name}", dependencies=[Depends(login_required)])
async def delete_credential(name: str):
    try: return await make_credentials_client().delete_credential(name)
    except Exception as e: raise HTTPException(status_code=502, detail=f"proxy credentials error: {e}")
```
Wire into `main.py` (`from app.routes import ... credentials_routes` + `include_router`).

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): /api/credentials (provider key vault, values never returned)`.

---

## Task 4: /api/models/test + /api/models/health (TDD)

**Files:** Create `ui/app/routes/models_routes.py`, `ui/tests/test_models_routes.py`; Modify `ui/app/main.py`.

- [ ] **Step 1: failing tests** (reuse `LitellmClient` with a `make_litellm_client` seam, or a dedicated fake):
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
    async def test_connection(self, litellm_params, mode): return {"status":"success","result":{"ok":True}}
    async def health(self): return {"healthy_endpoints":[{"model":"gpt-4o"}], "unhealthy_endpoints":[], "healthy_count":1, "unhealthy_count":0}

def test_test_requires_login(tmp_path):
    c=_client(tmp_path,FakeModels()); c.cookies.clear()
    assert c.post("/api/models/test", json={"litellm_params":{"model":"openai/gpt-4o"}}).status_code==401
def test_test_connection(tmp_path):
    r=_client(tmp_path,FakeModels()).post("/api/models/test", json={"litellm_params":{"model":"openai/gpt-4o","api_key":"sk-x"},"mode":"chat"})
    assert r.status_code==200 and r.json()["status"]=="success"
def test_health(tmp_path):
    r=_client(tmp_path,FakeModels()).get("/api/models/health")
    assert r.json()["healthy_count"]==1
```

- [ ] **Step 2: run red** → FAIL. **Step 3:** add `test_connection`/`health` to a client + implement the routes.
First extend `LitellmClient` (ui/app/litellm_client.py) with:
```python
    async def test_connection(self, litellm_params: dict, mode: str = "chat") -> dict:
        async with self._client() as c:
            r = await c.post(f"{self._base}/health/test_connection",
                             json={"litellm_params": litellm_params, "mode": mode})
            return {"status": "success", "result": r.json()} if r.status_code < 400 else {"status": "error", "result": r.text}

    async def health_all(self) -> dict:
        async with self._client() as c:
            r = await c.get(f"{self._base}/health"); r.raise_for_status(); return r.json()
```
Then `ui/app/routes/models_routes.py`:
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
Wire into `main.py`. (The test seam `make_models_client` is what tests monkeypatch; for `test_connection`/`health` the fake provides those methods.)

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): /api/models/test (pre-save) + /api/models/health (cached)`.

---

## Task 5: frontend — Provider Keys screen + nav

**Files:** Modify `ui/frontend/src/lib/api.js`, `ui/frontend/src/App.svelte`; Create `ui/frontend/src/routes/ProviderKeys.svelte`.

- [ ] **Step 1: api.js:**
```javascript
  credentials: () => req('/api/credentials'),
  createCredential: (b) => req('/api/credentials', { method:'POST', body: JSON.stringify(b) }),
  deleteCredential: (name) => req(`/api/credentials/${encodeURIComponent(name)}`, { method:'DELETE' }),
```
- [ ] **Step 2: `ProviderKeys.svelte`** — list (masked), add (name + provider + key), delete:
```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { PROVIDERS } from '../lib/providers.js'
  let creds = $state([]), err = $state(''), busy = $state(false), showAdd = $state(false)
  let form = $state({ credential_name:'', provider:'openai', api_key:'' })
  async function load(){ try{ creds = await api.credentials() }catch(e){ err=e.message } }
  onMount(load)
  async function add(){ busy=true; err=''
    try{ await api.createCredential({ credential_name:form.credential_name, provider:form.provider, api_key:form.api_key });
         form={credential_name:'',provider:'openai',api_key:''}; showAdd=false; await load() }
    catch(e){ err=e.message } finally{ busy=false } }
  async function del(n){ if(!confirm(`Delete credential "${n}"? Models using it will fail.`))return; busy=true
    try{ await api.deleteCredential(n); await load() }catch(e){ err=e.message } finally{ busy=false } }
</script>
<div class="page"><header><h1>Provider Keys</h1><button class="primary" onclick={()=>showAdd=!showAdd}>＋ Add key</button></header>
  {#if err}<div class="banner err">{err}</div>{/if}
  <p class="hint">Keys are stored encrypted in the proxy's database (via LITELLM_SALT_KEY) and reused by models. Values are never shown again.</p>
  {#if showAdd}<div class="card add">
    <label>Name <input bind:value={form.credential_name} placeholder="e.g. openai_prod" /></label>
    <label>Provider <select bind:value={form.provider}>{#each PROVIDERS as p}<option value={p.id}>{p.label}</option>{/each}</select></label>
    <label>API key <input type="password" bind:value={form.api_key} placeholder="sk-…" /></label>
    <div class="row"><button class="primary" onclick={add} disabled={busy||!form.credential_name||!form.api_key}>Save key</button><button onclick={()=>showAdd=false}>Cancel</button></div>
  </div>{/if}
  <div class="card">{#if creds.length===0}<p class="empty">No provider keys yet.</p>{:else}
    <table><thead><tr><th>Name</th><th>Provider</th><th></th></tr></thead><tbody>
      {#each creds as k}<tr><td>{k.credential_name}</td><td>{k.credential_info?.provider||'—'}</td>
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
- [ ] **Step 3: App.svelte** — `import ProviderKeys`; add to the **Access** nav group: `<button class="nav" class:active={screen==='providerkeys'} onclick={()=>screen='providerkeys'}>🗝 Provider Keys</button>`; render branch `{:else if screen==='providerkeys'}<ProviderKeys />`.
- [ ] **Step 4: build** + commit `feat(ui): Provider Keys screen (DB-backed credential vault)`.

---

## Task 6: frontend — Models v2 (credential / mode / costs / test / health)

**Files:** Modify `ui/frontend/src/routes/Models.svelte`, `ui/frontend/src/lib/api.js`.

- [ ] **Step 1: api.js:** `testModel: (b)=>req('/api/models/test',{method:'POST',body:JSON.stringify(b)})`, `modelsHealth: ()=>req('/api/models/health')`.
- [ ] **Step 2: Models.svelte** — extend the add form + table. In `<script>`:
  - load `credentials` (`api.credentials()`) for the dropdown; load `modelsHealth()` for the health column (map by model_name from `healthy_endpoints`/`unhealthy_endpoints`).
  - form gains: `credential` (selected credential_name | ''), `mode` (default 'chat'), `input_cost`, `output_cost`, plus the existing fields.
  - `buildLitellmParams` extension: if `credential` set → add `litellm_credential_name: credential` (omit api_key); else keep the env-var path. Add `input_cost_per_token`/`output_cost_per_token` (numbers) when provided. Put `mode` into `model_info: { mode }`.
  - **Test button**: `await api.testModel({ litellm_params: builtParams, mode })` → show success/error inline. Works before Save (no save first).
- [ ] **Step 3: form additions (markup):**
```svelte
  <label>Credential
    <select bind:value={form.credential}>
      <option value="">— inline / env var —</option>
      {#each credentials as c}<option value={c.credential_name}>{c.credential_name}</option>{/each}
    </select>
  </label>
  <label>Mode <select bind:value={form.mode}>
    {#each ['chat','embedding','completion','image_generation','audio_transcription','rerank','moderations'] as m}<option value={m}>{m}</option>{/each}
  </select></label>
  <label>Input cost / token <input type="number" step="1e-9" min="0" bind:value={form.input_cost} placeholder="auto from catalog (v2.3)" /></label>
  <label>Output cost / token <input type="number" step="1e-9" min="0" bind:value={form.output_cost} placeholder="auto from catalog (v2.3)" /></label>
  <div class="row">
    <button onclick={testConn} disabled={busy||!form.modelName||!form.modelId}>Test connection</button>
    <button class="primary" onclick={addModel} disabled={store.applying||!form.modelName||!form.modelId}>Save</button>
    <button onclick={resetForm}>Cancel</button>
  </div>
  {#if testResult}<div class="banner {testResult.ok?'ok':'err'}">{testResult.msg}</div>{/if}
```
- [ ] **Step 4: table health column** — add a "Health" column showing a green/red dot per `m.model_name` from the loaded health map (or "—" if unknown). Save now stages (per v2.1) — the button says **Save**; the global Apply bar applies.
- [ ] **Step 5: build** + commit `feat(ui): Models v2 — credential/mode/costs/test-connection/health`.

---

## Task 7: enable cached per-model health (general_settings)

**Files:** none new — set via the UI/config.

- [ ] **Step 1:** Document + default: add `general_settings.background_health_checks: true` and `health_check_interval: 300` to the config (the UI can write it on first Models load, or it's documented as a one-time config edit applied via the bar). Add a test in `test_config_store.py` that these keys round-trip (they're preserved by `extra="allow"`). Commit `feat(ui): enable cached background health checks`.

---

## Task 8: real-stack integration verification

- [ ] **Step 1:** build + up; log in. **Provider Keys**: add `openai_test` (provider openai, key sk-fake) → appears masked; `curl /credentials` shows it.
- [ ] **Step 2:** **Models** add: pick credential `openai_test`, model `gpt-4o-mini`, mode chat → **Test connection** (expect error with a fake key, but confirms the endpoint round-trips a result) → **Save** → Apply bar → Apply → model live; the model row shows a health dot.
- [ ] **Step 3:** Confirm the config-only credential survives an Apply (restart) per the Task-1 spike outcome (DB or config_list path). Delete the credential + model; tear down; restore config.

## Self-Review
- **Spec coverage:** provider-key vault (T1–T3,T5) ✓; credential dropdown + mode + costs + test-pre-save + health (T4,T6,T7) ✓; master key server-side + values never returned (T3) ✓; spike-before-build (T1) ✓.
- **Placeholders:** Task 6's `buildLitellmParams`/`testConn` wiring references the existing Models.svelte patterns + gives the exact new fields/markup; the implementer extends the known file (acceptable — full markup + the field list are explicit).
- **Type consistency:** `CredentialsClient.{list,create,delete}_credential(s)`, `make_credentials_client`/`make_models_client` seams, `LitellmClient.{test_connection,health_all}`, `api.{credentials,createCredential,deleteCredential,testModel,modelsHealth}` consistent.

## Follow-on
v2.3 (catalog syncs) auto-fills the cost/mode/endpoint fields added here. Executed together as one phased goal.
