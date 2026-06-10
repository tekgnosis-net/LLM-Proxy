# LLM-Proxy Admin UI — v3.8 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Backend = TDD (`cd ui && .venv/bin/python -m pytest -q`). Frontend (Svelte 5) = build (`cd ui/frontend && npm run build`) + Playwright on a **LAN-IP origin** (`http://10.0.20.85:8081`, NOT localhost). Steps use `- [ ]`. **Branch: `v3.8-keys-auth-usage`** (already created).

**Goal:** Editable virtual keys, a UI admin-password change, and Usage range/auto-refresh persistence.

**Architecture:** Mostly additive. Keys gain an Edit flow over a new `/key/update` pass-through. The admin password gains a DB override hash (`ui_admin_auth`) that auth resolves before the env var; the verify-old-then-hash-new logic is a pure, TDD'd function. Usage persists its range + a polling interval in `localStorage`.

**Tech Stack:** FastAPI, asyncpg, argon2, httpx, Svelte 5 runes, localStorage.

**Spec:** [`../specs/2026-06-10-llm-proxy-ui-v3.8-design.md`](../specs/2026-06-10-llm-proxy-ui-v3.8-design.md).

---

## File Structure
```
ui/frontend/src/routes/Usage.svelte       # MODIFY: persist days + refreshSec, auto-refresh timer
ui/app/keys_client.py                      # MODIFY: add update_key (POST /key/update)
ui/app/routes/keys_routes.py               # MODIFY: add POST /api/keys/update
ui/frontend/src/routes/Keys.svelte         # MODIFY: Edit flow + inherit-global strategy default
ui/app/admin_auth.py                       # CREATE: effective_hash / set_hash / verify_and_hash
ui/tests/test_admin_auth.py                # CREATE: TDD verify_and_hash
ui/app/routes/auth_routes.py               # MODIFY: async login (resolve effective_hash) + change-password
ui/frontend/src/routes/Settings.svelte     # MODIFY: change-password card
```

---

## Task 1: Usage — persist range + auto-refresh

**Files:** Modify `ui/frontend/src/routes/Usage.svelte`. **READ it first** (v3.7.1: `days` state, `load()`, `$effect(() => { days; load() })`).

- [ ] **Step 1:** Initialise both prefs from `localStorage` and add the interval state:
```javascript
  function initDays() { const v = +localStorage.getItem('usage.days'); return [7,30,90].includes(v) ? v : 30 }
  function initRefresh() { return +localStorage.getItem('usage.refreshSec') || 0 }  // 0 = off
  let days = $state(initDays())
  let refreshSec = $state(initRefresh())
  let timer = null
```
- [ ] **Step 2:** Replace the existing `$effect(() => { days; load() })` with two effects (persist + react), and add timer arming:
```javascript
  import { onMount, onDestroy } from 'svelte'
  $effect(() => { localStorage.setItem('usage.days', days); load() })          // range change → save + reload
  $effect(() => { localStorage.setItem('usage.refreshSec', refreshSec); arm() }) // interval change → save + re-arm
  function arm() {
    if (timer) { clearInterval(timer); timer = null }
    if (refreshSec > 0 && !document.hidden) timer = setInterval(load, refreshSec * 1000)
  }
  function onVis() { arm() }                       // pause when hidden, resume when visible
  onMount(() => document.addEventListener('visibilitychange', onVis))
  onDestroy(() => { if (timer) clearInterval(timer); document.removeEventListener('visibilitychange', onVis) })
```
(If `onMount` was already imported, don't duplicate it. Drop any now-redundant `onMount(load)` — the `days` effect loads on mount.)
- [ ] **Step 3:** Add the Auto-refresh control next to the range buttons:
```svelte
  <label class="refresh">Auto-refresh
    <select bind:value={refreshSec}>
      <option value={0}>Off</option><option value={10}>10s</option><option value={30}>30s</option>
      <option value={60}>60s</option><option value={300}>5m</option>
    </select>
  </label>
```
Add a `.refresh{margin-left:auto;font-size:13px;color:#6e6e73}` style and make the range row `display:flex;align-items:center` so the control sits to the right.
- [ ] **Step 4:** Build `cd ui/frontend && npm run build` → succeeds. Commit `git add ui/frontend/src/routes/Usage.svelte && git commit -m "feat(ui): Usage remembers range + a saved auto-refresh interval (polls in place, pauses when hidden)"`

---

## Task 2: Editable virtual keys

### Part A — backend update_key + route
**Files:** Modify `ui/app/keys_client.py`, `ui/app/routes/keys_routes.py`.

- [ ] **Step 1:** Add to `KeysClient` (after `generate_key`):
```python
    async def update_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/key/update", json=payload)
            r.raise_for_status()
            return r.json()
```
- [ ] **Step 2:** Add the route to `keys_routes.py` (mirror `create_key`'s try/except shape):
```python
@router.post("/keys/update", dependencies=[Depends(login_required)])
async def update_key(payload: dict = Body(...)):
    try:
        return await make_keys_client().update_key(payload)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text[:300])
```
(Match the existing import set + error handling in `create_key`; reuse its exact `except` style if it differs.)
- [ ] **Step 3:** Backend suite green (`cd ui && .venv/bin/python -m pytest -q`). Commit `git add ui/app/keys_client.py ui/app/routes/keys_routes.py && git commit -m "feat(ui): /api/keys/update -> LiteLLM /key/update (pass-through)"`

### Part B — Keys.svelte edit flow
**Files:** Modify `ui/frontend/src/routes/Keys.svelte`. **READ it first** (create form, `router_strategy`, the v3.6 router knobs, the one-time-key banner).

- [ ] **Step 4:** Add `let editingToken = $state(null)` and `editKey(k)` to pre-fill the form from a key row:
```javascript
  function editKey(k) {
    form = { ...form,
      key_alias: k.key_alias || '', models: (k.models || []).join(', '),
      max_budget: k.max_budget ?? '', tpm_limit: k.tpm_limit ?? '', rpm_limit: k.rpm_limit ?? '',
      router_strategy: (k.router_settings?.routing_strategy) || '',
      router_num_retries: k.router_settings?.num_retries ?? '', router_timeout: k.router_settings?.timeout ?? '',
      router_cooldown_time: k.router_settings?.cooldown_time ?? '', router_allowed_fails: k.router_settings?.allowed_fails ?? '',
      router_retry_after: k.router_settings?.retry_after ?? '' }
    editingToken = k.token; showCreate = true; showRouterSettings = !!k.router_settings
  }
```
(Match the actual `form` field names in this file; adjust keys that differ.)
- [ ] **Step 5:** Add an **Edit** button per key row (next to delete). In `create()`, branch on `editingToken`:
```javascript
    if (editingToken) {
      await api.post('/api/keys/update', { key: editingToken, ...buildKeyFields() })   // reuse the same field assembly
      editingToken = null; showCreate = false; await refresh()
      return
    }
    // ...existing generate path (POST /api/keys), shows the one-time secret banner...
```
Factor the alias/models/budgets/limits/`router_settings` assembly the create path already does into a `buildKeyFields()` helper both paths call (DRY). The "＋ New key" button sets `editingToken = null` first. The form heading reads `{editingToken ? 'Edit key' : 'Create key'}`; hide the one-time-secret banner when `editingToken`.
- [ ] **Step 6:** **Inherit-global default** — the new-key `routing_strategy` select gets a first `<option value="">Inherit global</option>` and `form.router_strategy` defaults to `''` (not `cost-based-routing`). In `create()`, only add `routing_strategy` to `router_settings` when non-empty (likely already the case for the knobs — apply the same to the strategy).
- [ ] **Step 7:** Build → succeeds. Commit `git add ui/frontend/src/routes/Keys.svelte && git commit -m "feat(ui): edit a virtual key in place (router settings/budgets/limits); default new-key strategy to inherit-global"`

---

## Task 3: Change admin password

### Part A — admin_auth module (TDD the pure bit)
**Files:** Create `ui/app/admin_auth.py`, `ui/tests/test_admin_auth.py`.

- [ ] **Step 1: Failing tests** — `ui/tests/test_admin_auth.py`:
```python
import pytest
from fastapi import HTTPException
from app.auth import hash_password, verify_password
from app.admin_auth import verify_and_hash

def test_verify_and_hash_ok():
    eff = hash_password("oldpass123")
    h = verify_and_hash("oldpass123", "newpass456", eff)
    assert verify_password("newpass456", h) and not verify_password("oldpass123", h)

def test_verify_and_hash_wrong_old():
    eff = hash_password("oldpass123")
    with pytest.raises(HTTPException) as e:
        verify_and_hash("WRONG", "newpass456", eff)
    assert e.value.status_code == 401

def test_verify_and_hash_short_new():
    eff = hash_password("oldpass123")
    with pytest.raises(HTTPException) as e:
        verify_and_hash("oldpass123", "short", eff)
    assert e.value.status_code == 422
```
- [ ] **Step 2: Run → FAIL** (`app.admin_auth` missing). `cd ui && .venv/bin/python -m pytest tests/test_admin_auth.py -v`.
- [ ] **Step 3: Create `ui/app/admin_auth.py`:**
```python
from __future__ import annotations
import asyncpg
from fastapi import HTTPException
from app.auth import verify_password, hash_password
from app.settings import get_settings

_DDL = """CREATE TABLE IF NOT EXISTS ui_admin_auth (
  id int PRIMARY KEY DEFAULT 1,
  password_hash text NOT NULL,
  updated_at timestamptz DEFAULT now(),
  CONSTRAINT ui_admin_auth_single_row CHECK (id = 1))"""


def verify_and_hash(old: str, new: str, eff: str) -> str:
    """Pure: verify `old` against the effective hash, enforce a min length on `new`,
    return a fresh argon2 hash of `new`. Raises 401 (bad old) / 422 (weak new)."""
    if not verify_password(old, eff):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    if len(new or "") < 8:
        raise HTTPException(status_code=422, detail="new password must be at least 8 characters")
    return hash_password(new)


async def effective_hash() -> str:
    """The admin hash in effect: the DB override if set, else the env ADMIN_PASSWORD_HASH."""
    s = get_settings()
    if not s.database_url:
        return s.admin_password_hash
    conn = await asyncpg.connect(s.database_url)
    try:
        await conn.execute(_DDL)
        row = await conn.fetchrow("SELECT password_hash FROM ui_admin_auth WHERE id = 1")
    finally:
        await conn.close()
    return row["password_hash"] if row else s.admin_password_hash


async def set_hash(h: str) -> None:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(_DDL)
        await conn.execute(
            "INSERT INTO ui_admin_auth (id, password_hash, updated_at) VALUES (1, $1, now()) "
            "ON CONFLICT (id) DO UPDATE SET password_hash = $1, updated_at = now()", h)
    finally:
        await conn.close()
```
- [ ] **Step 4: Run → PASS**; full suite green. Commit `git add ui/app/admin_auth.py ui/tests/test_admin_auth.py && git commit -m "feat(ui): admin_auth — DB override hash + verify_and_hash (TDD)"`

### Part B — async login + change-password route
**Files:** Modify `ui/app/routes/auth_routes.py`.

- [ ] **Step 5:** Make `login` async and resolve the effective hash; add the change-password route:
```python
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from app.auth import verify_password, login_required
from app.admin_auth import effective_hash, set_hash, verify_and_hash

# ... existing LoginBody ...

@router.post("/login")
async def login(body: LoginBody, request: Request):
    if not verify_password(body.password, await effective_hash()):
        raise HTTPException(status_code=401, detail="invalid password")
    request.session["authed"] = True
    return {"ok": True}


class ChangePwBody(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password", dependencies=[Depends(login_required)])
async def change_password(body: ChangePwBody):
    h = verify_and_hash(body.old_password, body.new_password, await effective_hash())
    await set_hash(h)
    return {"ok": True}
```
- [ ] **Step 6:** Full suite green. Commit `git add ui/app/routes/auth_routes.py && git commit -m "feat(ui): login resolves DB-or-env admin hash; POST /api/auth/change-password"`

### Part C — Settings.svelte card
**Files:** Modify `ui/frontend/src/routes/Settings.svelte`. **READ it first** (match its card/section style + the `api` helper).

- [ ] **Step 7:** Add a "Change admin password" card: three password inputs (Current / New / Confirm) + a Save button → `api.post('/api/auth/change-password', { old_password, new_password })`. Client-side guards: New === Confirm and `New.length >= 8` (disable Save otherwise). Show inline success ("Password changed") / error (from the response detail — 401 "current password is incorrect", 422 "at least 8 characters"). Clear the fields on success.
- [ ] **Step 8:** Build → succeeds; backend suite green. Commit `git add ui/frontend/src/routes/Settings.svelte && git commit -m "feat(ui): Settings — change admin password"`

---

## Task 4: Integration verification + release

- [ ] **Step 1:** Local-build stack (`docker-compose.override.yml` → `build: ./ui`); seed config; `docker compose up -d --build --wait`; Playwright on **`http://10.0.20.85:8081`** (LAN-IP, hard-reload).
- [ ] **Step 2 — Usage (#3,#4):** open Usage, pick 7d + 30s auto-refresh → reload the page → both restored; issue 1-2 chat completions (master key) → within ~30s the table updates with no manual reload; background the tab → confirm polling pauses (no new `/api/usage/summary` calls in the network log).
- [ ] **Step 3 — editable keys (#1):** create a key → Edit it → change `rpm_limit` + per-key `timeout` + strategy → Save → `/api/keys/update` 200; reopen Edit (or `/key/list`) → the new values persisted. Confirm a brand-new key's strategy defaults to "Inherit global" (blank), not cost-based.
- [ ] **Step 4 — admin password (#2):** Settings → change password (correct current → new ≥ 8) → success; **Sign out → sign in with the NEW password** (works) and confirm the OLD password now fails. Then verify a wrong-current attempt shows "current password is incorrect" and a 6-char new shows the length error.
- [ ] **Step 5:** Full backend suite green; screenshots (`v38-usage-autorefresh.png`, `v38-key-edit.png`, `v38-change-password.png`) → `docs/images/`; teardown; restore `config/config.yaml` from example; remove override; `git status` clean.
- [ ] **Step 6 — release:** merge `v3.8-keys-auth-usage` → `main` (`--no-ff`), push → CI cuts **`1.18.0`** + image; bump compose/admin-ui pin to `1.18.0` (rebase past the release commit); push.

## Self-Review
- **Spec coverage:** Usage retain+refresh (#3,#4) → T1; editable keys (#1) → T2 (update_key + route + edit flow + inherit-global); admin password (#2) → T3 (admin_auth TDD + async login + change-password + Settings card); verify+release → T4. ✓
- **Type consistency:** `verify_and_hash(old,new,eff)->str` / `effective_hash()->str` / `set_hash(h)` (T3) used by auth_routes; `KeysClient.update_key(payload)` (T2A) used by the `/api/keys/update` route + `Keys.svelte` `editingToken` flow; `buildKeyFields()` shared by create+update; `localStorage["usage.days"|"usage.refreshSec"]` (T1). ✓
- **Placeholders:** all backend code + tests complete; frontend steps give exact state vars, handlers, markup, and the localStorage keys. The two "match the actual field names in this file" notes are deliberate (the implementer reads Keys.svelte/Settings.svelte first), not gaps. ✓
