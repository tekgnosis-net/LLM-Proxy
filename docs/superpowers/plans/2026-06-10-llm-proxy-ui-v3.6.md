# LLM-Proxy Admin UI — v3.6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Backend = TDD (`cd ui && .venv/bin/python -m pytest -q`). Frontend (Svelte 5) = build (`cd ui/frontend && npm run build`) + Playwright on a **LAN-IP origin** (`http://10.0.20.85:8081`, NOT localhost — secure-context lesson). Steps use `- [ ]`. **Branch: `v3.6-keys-validation`** (already created).

**Goal:** Editable Provider Keys (keep-existing-key on blank), per-key router reliability knobs, and a keyless-model warning.

**Architecture:** A new `_credential_data(name, data, store)` helper in the v3 config route reuses the existing encrypted key when the staged `api_key` is blank — the only backend change. Everything else is frontend: a Provider-Keys edit flow, five numeric fields on the key Router-Settings block, and a non-blocking warning in the model save path.

**Tech Stack:** FastAPI + asyncpg, Svelte 5 runes, httpx, docker-compose.

**Spec:** [`../specs/2026-06-10-llm-proxy-ui-v3.6-design.md`](../specs/2026-06-10-llm-proxy-ui-v3.6-design.md).

---

## File Structure
```
ui/app/routes/config_v3_routes.py        # MODIFY: _credential_data helper + use it in stage_item
ui/frontend/src/routes/ProviderKeys.svelte  # MODIFY: Edit flow (keep-blank-key)
ui/frontend/src/routes/Keys.svelte        # MODIFY: 5 numeric router knobs
ui/frontend/src/routes/Models.svelte      # MODIFY: keyless-model warning
```

---

## Task 1: Editable Provider Keys

### Part A — backend keep-existing-key (TDD)
**Files:** Modify `ui/app/routes/config_v3_routes.py`. Test: `ui/tests/test_config_v3_routes.py` (or wherever the route tests live — grep `test_config_v3`).

- [ ] **Step 1: Failing tests.** Add to the route test file (the repo uses `pytestmark = pytest.mark.asyncio` style — match it):
```python
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
    from app.routes.config_v3_routes import _credential_data
    out = await _credential_data("K", {"provider": "openai", "api_key": "sk-real"}, _FakeStore([]))
    assert out["provider"] == "openai" and out["value_encrypted"] and out["value_encrypted"] != "sk-real"
```
- [ ] **Step 2: Run → FAIL** (`_credential_data` undefined). `cd ui && .venv/bin/python -m pytest tests/ -k credential_data -v`.
- [ ] **Step 3: Add the helper** in `config_v3_routes.py` (above `stage_item`; `effective` and `_fernet` are already imported/defined in this file):
```python
async def _credential_data(name: str, data: dict, store) -> dict:
    """Build a credential's stored data. A provided api_key is Fernet-encrypted; a
    BLANK api_key reuses the existing credential's value_encrypted (edit without
    re-typing the secret). Blank with no existing credential is rejected."""
    data = data or {}
    provider = data.get("provider")
    api_key = data.get("api_key")
    if api_key:
        ve = _fernet().encrypt(api_key.encode()).decode()
    else:
        eff = effective(await store.applied(), await store.staged())
        existing = next((i for i in eff if i["kind"] == "credential" and i["name"] == name
                         and i.get("flag") != "deleted"), None)
        ve = (existing.get("data") or {}).get("value_encrypted") if existing else None
        if not ve:
            raise HTTPException(status_code=422, detail="credential api_key required (no existing key to keep)")
    return {"provider": provider, "value_encrypted": ve}
```
- [ ] **Step 4: Wire it into `stage_item`** — replace the current credential branch (lines ~47-51) with:
```python
    if kind == "credential":
        data = await _credential_data(name, data, make_config_store())
```
- [ ] **Step 5: Run → PASS**; full suite green (`pytest -q`).
- [ ] **Step 6: Commit** `git add ui/app/routes/config_v3_routes.py ui/tests && git commit -m "feat(ui): credential stage reuses existing key when api_key blank (editable provider keys)"`

### Part B — ProviderKeys.svelte edit flow
**Files:** Modify `ui/frontend/src/routes/ProviderKeys.svelte`. **READ it first.**

- [ ] **Step 7:** Add `let editingName = $state(null)`. Add an **Edit** button per non-deleted credential row → `editCred(item)`:
```javascript
  function editCred(item) {
    form = { credential_name: item.name, provider: item.data?.provider || 'openai', api_key: '' }
    editingName = item.name; showAdd = true; err = ''
  }
```
- [ ] **Step 8:** In the add/edit form: the **Name** input is `disabled={!!editingName}` (rename = delete+re-add). The **API key** field placeholder becomes `editingName ? 'leave blank to keep the current key' : 'sk-…'` and is **not required** when editing (`disabled`/required logic: when `editingName`, allow blank). The form heading reads `{editingName ? 'Edit key' : 'Add key'}`.
- [ ] **Step 9:** `add()` → rename to `save()`: it already calls `store.stageItem('credential', form.credential_name, { provider, api_key })`. For edit, the name is `editingName` (same). Allow an empty `api_key` to pass to the backend (which now keeps the existing key). On success reset the form AND `editingName = null`. The "＋ Add key" header button sets `editingName = null` before opening (fresh add). Validation: block submit only if `!form.credential_name`, or (`!editingName && !form.api_key`) — i.e. a NEW key still needs a value, an edit doesn't.
- [ ] **Step 10:** Build `cd ui/frontend && npm run build` → succeeds. Commit `git add ui/frontend/src/routes/ProviderKeys.svelte && git commit -m "feat(ui): edit a Provider Key in place (blank key keeps the current secret)"`

---

## Task 2: Per-key router reliability knobs (Virtual Keys)

**Files:** Modify `ui/frontend/src/routes/Keys.svelte`. **READ it first** (it has `form.router_strategy`/`router_fallbacks` + the `router_settings` assembly from v3.5).

- [ ] **Step 1:** Extend `form` with `router_num_retries: ''`, `router_timeout: ''`, `router_cooldown_time: ''`, `router_allowed_fails: ''`, `router_retry_after: ''`.
- [ ] **Step 2:** In the "Router Settings (optional)" block, add five `<input type="number" min="0">` fields labelled **Num retries**, **Timeout (s)** (`step="0.1"`), **Cooldown time (s)**, **Allowed fails**, **Retry after (s)**, each bound to its `form.router_*`, with a hint "blank = inherit global".
- [ ] **Step 3:** In `create()`, after building `rs` (the router_settings object) with strategy/fallbacks, add the numerics (only when non-empty):
```javascript
    for (const [k, v] of [['num_retries', form.router_num_retries], ['timeout', form.router_timeout],
        ['cooldown_time', form.router_cooldown_time], ['allowed_fails', form.router_allowed_fails],
        ['retry_after', form.router_retry_after]]) {
      if (v !== '' && v != null) rs[k] = Number(v)
    }
```
(`rs` is attached to `payload.router_settings` only when non-empty, as today.) Reset the five fields when the form closes/resets.
- [ ] **Step 4:** Build → succeeds. Commit `git add ui/frontend/src/routes/Keys.svelte && git commit -m "feat(ui): per-key router reliability knobs (retries/timeout/cooldown/allowed_fails/retry_after)"`

---

## Task 3: Keyless-model warning (Models)

**Files:** Modify `ui/frontend/src/routes/Models.svelte`. **READ `saveModel()`** (from v3.5).

- [ ] **Step 1:** Add `let pendingNoKey = $state(false)`. In `saveModel()`, before staging, gate on missing key:
```javascript
  async function saveModel() {
    if (!form.credential && !form.api_key_env && !pendingNoKey) { pendingNoKey = true; return }
    pendingNoKey = false
    const id = editingId || uuidv4()
    const ok = await store.stageItem('model', id, { model_name: form.modelName, litellm_params: buildParams(), model_info: { mode: form.mode } })
    if (ok) resetForm()
  }
```
- [ ] **Step 2:** In the markup, when `pendingNoKey`, show a warning banner above the Save button:
```svelte
  {#if pendingNoKey}
    <div class="banner warn">This deployment has no API key. LiteLLM requires one even for local providers
      (vLLM/llama.cpp) — requests will fail without it. Pick a saved credential (a reusable dummy key works)
      or set an API-key env var. Click Save again to save anyway.</div>
  {/if}
```
Add a `.banner.warn` style (amber). Clear `pendingNoKey` whenever `form.credential` or `form.api_key_env` becomes non-empty (and in `resetForm()`).
- [ ] **Step 3:** Build → succeeds; backend suite still green (`cd ui && .venv/bin/python -m pytest -q`). Commit `git add ui/frontend/src/routes/Models.svelte && git commit -m "feat(ui): warn before saving a model with no API key"`

---

## Task 4: Integration verification + release

- [ ] **Step 1:** Local-build stack; seed config; `docker compose up -d --build --wait`; catalog sync; Playwright on **`http://10.0.20.85:8081`** (LAN-IP, hard-reload).
- [ ] **Step 2 — editable keys (#3):** Provider Keys → create `DUMMY` (provider `openai_compatible`, key `dummy`). Apply. Then **Edit** `DUMMY` → change provider to `hosted_vllm`, **leave key blank** → Apply. Read the host/container `config.yaml` credential_list (root container `cat` or a test-connection) → confirm the `DUMMY` key is **still `dummy`** (preserved) and provider updated. Then Edit again → enter a new key → confirm it changed.
- [ ] **Step 3 — per-key knobs (#1):** create a key, expand Router Settings, set strategy + a timeout + num_retries → on create, capture the `/key/generate` payload (Network) → `router_settings` carries `routing_strategy`, `timeout`, `num_retries`.
- [ ] **Step 4 — keyless warning (#2):** Add model, pick a provider, leave credential + env-var blank → Save → warning appears; pick the `DUMMY` credential → warning clears; or click Save again → saves anyway.
- [ ] **Step 5:** Full backend suite green; screenshots (edit-key form, key router knobs, keyless warning) into `docs/images/`; teardown; restore config; `git status` clean.
- [ ] **Step 6 — release:** merge `v3.6-keys-validation` → `main` (`--no-ff`), push → CI cuts **`1.16.0`** + image; bump compose pin to `1.16.0` (rebase past the release commit); push.

## Self-Review
- **Spec coverage:** #1 per-key knobs → T2; #2 model validation → T3; #3 editable keys → T1 (backend keep-key + FE edit); verify+release → T4. ✓
- **Type consistency:** `_credential_data(name, data, store)` returns `{provider, value_encrypted}` (T1) used by `stage_item`; `editingName` (ProviderKeys) / `editingId` (Models, from v3.5) / `pendingNoKey` (Models); `form.router_*` numerics fold into the v3.5 `rs`/`payload.router_settings`. ✓
- **Placeholders:** backend helper + tests are complete; FE steps give the exact state vars + handlers + markup. ✓
