# LLM-Proxy Admin UI — Phase 3 (Virtual Keys + Budgets) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD (httpx MockTransport). Frontend = build + real-stack verification. Steps use `- [ ]`.

**Goal:** A **Virtual Keys** screen to list / create / delete LiteLLM virtual keys with budgets, models allowlist, and expiry — all via LiteLLM's key-management API, **master key server-side only**. The generated plaintext key is shown to the admin exactly once.

**Architecture:** New `keys_client` (async httpx, master-key auth) wraps `/key/generate`, `/key/list?return_full_object=true`, `/key/delete`, `/key/info`. New `keys_routes` (login-gated) proxy these; the list never exposes a plaintext key (only the hashed `token`, used for delete); create returns the one-time plaintext. A `Keys.svelte` screen + Create-Key sheet (models multi-select sourced from the current `config.model_list`).

**Tech Stack:** FastAPI, httpx, Pydantic; Svelte 5. No new deps.

**API reference (verified vs litellm `main`):** `POST /key/generate` → `{key (plaintext, once), token (hashed), ...}`; fields `key_alias, models ([]=all), max_budget, budget_duration ("30d"), duration (expiry), tpm_limit, rpm_limit, metadata`. `GET /key/list?return_full_object=true&page=&size=` → `{keys:[{token, key_alias, spend, max_budget, budget_duration, budget_reset_at, expires, models, tpm_limit, rpm_limit}], total_count, current_page, total_pages}`. `POST /key/delete {keys:[<hashed token or sk->]}` → `{deleted_keys}`.

---

## File Structure
```
ui/app/keys_client.py          # CREATE: async key API client
ui/app/routes/keys_routes.py   # CREATE: GET/POST /api/keys, POST /api/keys/delete
ui/app/main.py                 # MODIFY: include keys_routes
ui/tests/test_keys_client.py   # CREATE
ui/tests/test_keys_routes.py   # CREATE
ui/frontend/src/lib/api.js     # MODIFY: keys helpers
ui/frontend/src/routes/Keys.svelte  # CREATE
ui/frontend/src/App.svelte     # MODIFY: nav + render
```

---

## Task 1: keys_client (TDD)

**Files:** Create `ui/app/keys_client.py`, `ui/tests/test_keys_client.py`.

- [ ] **Step 1: failing tests** (`ui/tests/test_keys_client.py`):
```python
import httpx, pytest
from app.keys_client import KeysClient


def _client(handler):
    return KeysClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_keys_uses_full_object_and_auth():
    def handler(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        assert req.url.params.get("return_full_object") == "true"
        return httpx.Response(200, json={"keys": [{"token": "h1", "key_alias": "ci", "spend": 0.5, "max_budget": 10}], "total_count": 1})
    keys = await _client(handler).list_keys()
    assert keys[0]["key_alias"] == "ci"


@pytest.mark.asyncio
async def test_generate_key_returns_plaintext_once():
    def handler(req):
        assert req.url.path.endswith("/key/generate")
        body = httpx.Response  # noqa
        return httpx.Response(200, json={"key": "sk-NEWPLAINTEXT", "token": "hashed", "key_alias": "ci", "max_budget": 10})
    res = await _client(handler).generate_key({"key_alias": "ci", "max_budget": 10})
    assert res["key"] == "sk-NEWPLAINTEXT"


@pytest.mark.asyncio
async def test_delete_keys_posts_tokens():
    seen = {}
    def handler(req):
        if req.url.path.endswith("/key/delete"):
            import json
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"deleted_keys": ["h1"]})
        return httpx.Response(404)
    res = await _client(handler).delete_keys(["h1"])
    assert seen["body"] == {"keys": ["h1"]}
    assert res["deleted_keys"] == ["h1"]


@pytest.mark.asyncio
async def test_list_keys_error_raises():
    def handler(req):
        return httpx.Response(500, text="boom")
    with pytest.raises(httpx.HTTPError):
        await _client(handler).list_keys()
```

- [ ] **Step 2: run red** — `cd ui && .venv/bin/python -m pytest tests/test_keys_client.py -v` → FAIL (module missing).

- [ ] **Step 3: implement `ui/app/keys_client.py`:**
```python
from __future__ import annotations
import httpx
from typing import Any, Optional


class KeysClient:
    """Async client for LiteLLM key-management endpoints. Master key stays here
    (server-side); never returned to the browser except the one-time plaintext
    from generate()."""

    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def list_keys(self, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/key/list",
                            params={"return_full_object": "true", "page": page, "size": size})
            r.raise_for_status()
            data = r.json()
            keys = data.get("keys", data) if isinstance(data, dict) else data
            # normalize: keep only dict items (full objects); drop bare token strings
            return [k for k in keys if isinstance(k, dict)]

    async def generate_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/key/generate", json=payload)
            r.raise_for_status()
            return r.json()

    async def delete_keys(self, tokens: list[str]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/key/delete", json={"keys": tokens})
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4: run green + full suite** — `cd ui && .venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 5: commit** — `git add ui/app/keys_client.py ui/tests/test_keys_client.py && git commit -m "feat(ui): keys_client (list/generate/delete via litellm key API)"`

---

## Task 2: keys routes (TDD)

**Files:** Create `ui/app/routes/keys_routes.py`, `ui/tests/test_keys_routes.py`; Modify `ui/app/main.py`.

- [ ] **Step 1: failing tests** (`ui/tests/test_keys_routes.py`) — inject a fake client via a `make_keys_client` seam:
```python
import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.keys_routes as kr
    kr.make_keys_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


class FakeKeys:
    def __init__(self): self.deleted = None
    async def list_keys(self): return [{"token": "h1", "key_alias": "ci", "spend": 0.5, "max_budget": 10, "models": []}]
    async def generate_key(self, payload): return {"key": "sk-NEW", "token": "h2", **payload}
    async def delete_keys(self, tokens): self.deleted = tokens; return {"deleted_keys": tokens}


def test_keys_requires_login(tmp_path):
    c = _client(tmp_path, FakeKeys()); c.cookies.clear()
    assert c.get("/api/keys").status_code == 401


def test_list_keys(tmp_path):
    c = _client(tmp_path, FakeKeys())
    r = c.get("/api/keys"); assert r.status_code == 200
    assert r.json()[0]["key_alias"] == "ci"
    assert "key" not in r.json()[0]  # list never returns plaintext


def test_generate_key_returns_plaintext(tmp_path):
    c = _client(tmp_path, FakeKeys())
    r = c.post("/api/keys", json={"key_alias": "ci", "max_budget": 10})
    assert r.status_code == 200 and r.json()["key"] == "sk-NEW"


def test_delete_key(tmp_path):
    fake = FakeKeys(); c = _client(tmp_path, fake)
    r = c.post("/api/keys/delete", json={"tokens": ["h1"]})
    assert r.status_code == 200 and fake.deleted == ["h1"]
```

- [ ] **Step 2: run red** — FAIL (routes missing).

- [ ] **Step 3: implement `ui/app/routes/keys_routes.py`:**
```python
from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import login_required
from app.keys_client import KeysClient
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_keys_client() -> KeysClient:
    s = get_settings()
    return KeysClient(s.litellm_base_url, s.litellm_master_key)


@router.get("/keys", dependencies=[Depends(login_required)])
async def list_keys():
    try:
        return await make_keys_client().list_keys()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")


@router.post("/keys", dependencies=[Depends(login_required)])
async def create_key(payload: dict = Body(...)):
    try:
        return await make_keys_client().generate_key(payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")


@router.post("/keys/delete", dependencies=[Depends(login_required)])
async def delete_keys(body: dict = Body(...)):
    tokens = body.get("tokens") or []
    if not tokens:
        raise HTTPException(status_code=422, detail="no tokens provided")
    try:
        return await make_keys_client().delete_keys(tokens)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")
```

- [ ] **Step 4: wire into `ui/app/main.py`** — import and include:
```python
from app.routes import auth_routes, health_routes, config_routes, keys_routes
...
    app.include_router(keys_routes.router)
```

- [ ] **Step 5: run green + full suite** — `cd ui && .venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 6: commit** — `git add ui/app/routes/keys_routes.py ui/app/main.py ui/tests/test_keys_routes.py && git commit -m "feat(ui): /api/keys routes (list/create/delete, login-gated)"`

---

## Task 3: Keys screen + nav

**Files:** Modify `ui/frontend/src/lib/api.js`, `ui/frontend/src/App.svelte`; Create `ui/frontend/src/routes/Keys.svelte`.

- [ ] **Step 1: api.js — add helpers:**
```javascript
  keys: () => req('/api/keys'),
  createKey: (payload) => req('/api/keys', { method: 'POST', body: JSON.stringify(payload) }),
  deleteKey: (tokens) => req('/api/keys/delete', { method: 'POST', body: JSON.stringify({ tokens }) }),
```

- [ ] **Step 2: create `ui/frontend/src/routes/Keys.svelte`:**
```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let keys = $state([]); let err = $state(''); let loading = $state(false)
  let showCreate = $state(false); let busy = $state(false)
  let newKey = $state(null)   // the one-time plaintext key after create
  let availableModels = $state([])
  let form = $state({ key_alias: '', models: [], max_budget: '', budget_duration: '', duration: '', rpm_limit: '', tpm_limit: '' })

  async function load() {
    loading = true; err = ''
    try {
      keys = await api.keys()
      const cfg = await api.config().catch(() => ({}))
      availableModels = (cfg.model_list || []).map(m => m.model_name)
    } catch (e) { err = e.message } finally { loading = false }
  }
  onMount(load)

  function num(v) { return v === '' || v == null ? undefined : Number(v) }
  async function create() {
    busy = true; err = ''; newKey = null
    const payload = { key_alias: form.key_alias || undefined, models: form.models,
      max_budget: num(form.max_budget), budget_duration: form.budget_duration || undefined,
      duration: form.duration || undefined, rpm_limit: num(form.rpm_limit), tpm_limit: num(form.tpm_limit) }
    Object.keys(payload).forEach(k => payload[k] === undefined && delete payload[k])
    try { const res = await api.createKey(payload); newKey = res.key; showCreate = false; await load() }
    catch (e) { err = e.message } finally { busy = false }
  }
  async function del(token) {
    if (!confirm('Delete this key? Requests using it will stop working.')) return
    busy = true; err = ''
    try { await api.deleteKey([token]); await load() } catch (e) { err = e.message } finally { busy = false }
  }
  function budget(k) { return k.max_budget != null ? `$${(k.spend ?? 0).toFixed(2)} / $${k.max_budget}` : `$${(k.spend ?? 0).toFixed(2)}` }
</script>

<div class="page">
  <header><h1>Virtual Keys</h1><button class="primary" onclick={() => { showCreate = true; newKey = null }} disabled={busy}>＋ Create key</button></header>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if newKey}
    <div class="banner key">
      <strong>New key (copy it now — shown only once):</strong>
      <code>{newKey}</code>
      <button onclick={() => navigator.clipboard?.writeText(newKey)}>Copy</button>
      <button onclick={() => newKey = null}>Done</button>
    </div>
  {/if}

  {#if showCreate}
    <div class="card add">
      <label>Alias <input bind:value={form.key_alias} placeholder="e.g. ci-pipeline" /></label>
      <label>Models (none selected = all)
        <select multiple bind:value={form.models} size={Math.min(5, Math.max(2, availableModels.length))}>
          {#each availableModels as m}<option value={m}>{m}</option>{/each}
        </select>
      </label>
      <div class="grid">
        <label>Max budget ($) <input type="number" min="0" step="0.01" bind:value={form.max_budget} placeholder="e.g. 50" /></label>
        <label>Budget resets <input bind:value={form.budget_duration} placeholder="e.g. 30d" /></label>
        <label>Expires <input bind:value={form.duration} placeholder="e.g. 30d (blank = never)" /></label>
        <label>RPM limit <input type="number" min="0" bind:value={form.rpm_limit} /></label>
        <label>TPM limit <input type="number" min="0" bind:value={form.tpm_limit} /></label>
      </div>
      <div class="row"><button class="primary" onclick={create} disabled={busy}>Create</button><button onclick={() => showCreate = false}>Cancel</button></div>
    </div>
  {/if}

  <div class="card">
    {#if loading}<p class="empty">Loading…</p>
    {:else if keys.length === 0}<p class="empty">No virtual keys yet.</p>
    {:else}
      <table>
        <thead><tr><th>Alias</th><th>Models</th><th>Spend / budget</th><th>Expires</th><th></th></tr></thead>
        <tbody>
          {#each keys as k}
            <tr>
              <td>{k.key_alias || '—'}</td>
              <td>{(k.models && k.models.length) ? k.models.join(', ') : 'all'}</td>
              <td>{budget(k)}</td>
              <td>{k.expires ? new Date(k.expires).toLocaleDateString() : 'never'}</td>
              <td><button class="danger" onclick={() => del(k.token)} disabled={busy}>Delete</button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:1000px}
  header{display:flex;align-items:center;justify-content:space-between}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.add{display:flex;flex-direction:column;gap:10px;max-width:560px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  label{display:flex;flex-direction:column;font-size:13px;color:#3a3a3c;gap:4px}
  input,select{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit}
  table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .row{display:flex;gap:8px}
  button{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}button.danger{color:#ff3b30;border-color:#ffd0cc}button:disabled{opacity:.5}
  .banner{padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}
  .banner.key{background:#fff7e6;border:1px solid #ffe1a8;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .banner.key code{background:#fff;padding:4px 8px;border-radius:6px;border:1px solid #eed8a8;user-select:all}
  .empty{color:#6e6e73}
</style>
```

- [ ] **Step 3: wire into `App.svelte`** — import `Keys`, add a nav group "Access" + button, and render branch:
```svelte
  import Keys from './routes/Keys.svelte'
```
Sidebar (new group after Configuration):
```svelte
      <div class="navgroup">Access</div>
      <button class="nav" class:active={screen==='keys'} onclick={() => screen='keys'}>🔑 Virtual Keys</button>
```
Render switch: add `{:else if screen==='keys'}<Keys />`.

- [ ] **Step 4: build** `cd ui/frontend && npm run build` → success. Commit:
```bash
git add ui/frontend/src/lib/api.js ui/frontend/src/routes/Keys.svelte ui/frontend/src/App.svelte
git commit -m "feat(ui): Virtual Keys screen (create/list/delete with budgets)"
```

---

## Task 4: Real-stack integration verification

- [ ] **Step 1:** `docker compose build llm-proxy-ui && docker compose up -d --wait`; log in at `http://10.0.20.85:8081`.
- [ ] **Step 2:** Virtual Keys → Create key (alias `ci-test`, max budget 5, resets `30d`) → the one-time `sk-...` is shown; the key appears in the list with `$0.00 / $5`. Verify via `curl -s -H "Authorization: Bearer $MK" http://localhost:4000/key/list?return_full_object=true | grep ci-test`.
- [ ] **Step 3:** Delete the key in the UI → it disappears from the list and from `/key/list`.
- [ ] **Step 4:** Tear down. (No config.yaml change in this phase; keys live in Postgres.)

## Self-Review
- **Spec coverage:** list/create/delete keys with budgets/models/expiry ✓; master key server-side (only generate's one-time plaintext leaves) ✓; list never returns plaintext (only hashed token for delete) ✓; models multi-select from current config ✓.
- **Security:** `keys_client` holds the master key; routes are login-gated; the only plaintext key exposed is the freshly-generated one (by design — the admin must copy it).
- **Type consistency:** `KeysClient.{list_keys,generate_key,delete_keys}`, `make_keys_client` seam, `api.{keys,createKey,deleteKey}` consistent.

## Follow-on
Phase 4 (usage & spend), Phase 5 (caching + housekeeping + export/import + dark mode), then docs + screenshots + LiteLLM credit.
