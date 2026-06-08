# LLM-Proxy Admin UI — v3.3: Frontend Rewiring Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Frontend (Svelte 5) = build + real-stack (Playwright) verification (no FE unit tests). Steps use `- [ ]`. **Branch: `v3-master-servant`.**

**Goal:** Rewire the UI from the (now-removed) v2 config blob API to the v3 **item model** — every config screen reads `GET /api/config/state` and writes via `PUT/DELETE /api/config/item`, with **flag rendering** (new/changed accented, `deleted` struck-through), a DB-backed **Apply/Discard** bar, a **passthrough** editor, and a **rendered-config preview**.

**Architecture:** A rewritten `configStore` holds the effective items (each `{kind,name,data,flag}`) + pending/count from `/api/config/state`; screens consume `store.itemsOfKind(kind)` and call `store.stageItem`/`store.deleteItem`. Apply/Discard hit the v3 engine endpoints. The look stays Apple-HIG (v2); flags add color/strikethrough.

**Tech Stack:** Svelte 5 (runes), no new deps.

**Spec:** [`../specs/2026-06-08-llm-proxy-ui-v3-master-servant-config.md`](../specs/2026-06-08-llm-proxy-ui-v3-master-servant-config.md). Backend (built+verified): v3.2 `/api/config/*`. **The branch UI is currently broken (v2 routes gone) — this plan fixes it.**

**Verified endpoints:** `GET /api/config/state`→`{items:[{kind,name,data,flag}], pending, count}` (creds `data:{provider,api_key:"***"}`); `PUT /api/config/item`{kind,name,data} (cred data sends plaintext `api_key`); `DELETE /api/config/item/{kind}/{name}`; `POST /api/apply`→`{applied,servant:"healthy"|"unhealthy",detail?}`; `POST /api/discard?kind=&name=`; `GET /api/config/passthrough`→`{data,yaml}` / `PUT`{yaml}; `GET /api/config/rendered`→`{config}` (redacted); plus surviving `models/test`, `models/health`, `catalog*`, `keys`, `usage`, `housekeeping`, `health`, `auth`.

---

## File Structure
```
ui/frontend/src/lib/api.js              # MODIFY: add config/* item helpers; remove dead v2 ones
ui/frontend/src/lib/configStore.svelte.js  # REWRITE: item model
ui/frontend/src/App.svelte              # MODIFY: Apply/Discard bar from new store; servant notice
ui/frontend/src/routes/Routing.svelte   # REWIRE: router_setting items
ui/frontend/src/routes/Caching.svelte   # REWIRE: litellm_setting cache items (read-only display)
ui/frontend/src/routes/Models.svelte    # REWIRE: model items + catalog picker + credential dropdown
ui/frontend/src/routes/ProviderKeys.svelte # REWIRE: credential items + flag/strikethrough
ui/frontend/src/routes/Settings.svelte  # MODIFY: + passthrough editor (keep catalog sync, dark mode)
ui/frontend/src/routes/ConfigViewer.svelte # REWIRE: rendered preview (/api/config/rendered)
ui/frontend/src/routes/Dashboard.svelte # MODIFY: model count from /api/config/state
ui/app/apply.py, ui/app/config_store.py # CLEANUP (T8): remove dead v1/v2 file-diff helpers
```

---

## Task 1: api.js + configStore rewrite (item model)

**Files:** Modify `ui/frontend/src/lib/api.js`, rewrite `ui/frontend/src/lib/configStore.svelte.js`.

- [ ] **Step 1: api.js** — remove dead helpers (`config`, `putConfig`, `applyStatus`, `cacheInfo`, `credentials`, `createCredential`, `deleteCredential`) and add:
```javascript
  configState: () => req('/api/config/state'),
  stageItem: (kind, name, data) => req('/api/config/item', { method: 'PUT', body: JSON.stringify({ kind, name, data }) }),
  deleteItem: (kind, name) => req(`/api/config/item/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  configRendered: () => req('/api/config/rendered'),
  passthroughGet: () => req('/api/config/passthrough'),
  passthroughPut: (yaml) => req('/api/config/passthrough', { method: 'PUT', body: JSON.stringify({ yaml }) }),
```
Keep `apply`, `discard`, `health`, `keys*`, `usage`, `housekeeping*`, `testModel`, `modelsHealth`, `catalog*`, `auth`, `exportConfigUrl`.

- [ ] **Step 2: REWRITE `configStore.svelte.js`** to the item model:
```javascript
import { api } from './api.js'

export function createConfigStore() {
  let items = $state([])        // [{kind,name,data,flag}]
  let loading = $state(false), saving = $state(false), applying = $state(false)
  let error = $state(''), notice = $state('')
  let pending = $state(false), count = $state(0)

  async function load() {
    loading = true; error = ''
    try { const s = await api.configState(); items = s.items || []; pending = s.pending; count = s.count }
    catch (e) { error = e.message } finally { loading = false }
  }
  function itemsOfKind(kind) { return items.filter(i => i.kind === kind) }
  function itemNamed(kind, name) { return items.find(i => i.kind === kind && i.name === name) }

  async function stageItem(kind, name, data) {
    saving = true; error = ''; notice = ''
    try { const r = await api.stageItem(kind, name, data); pending = r.pending; count = r.count; await load()
      notice = 'Staged. Click Apply to make it live.'; return true }
    catch (e) { error = e.status === 422 ? `Rejected: ${e.message}` : e.message; return false }
    finally { saving = false }
  }
  async function deleteItem(kind, name) {
    saving = true; error = ''; notice = ''
    try { const r = await api.deleteItem(kind, name); pending = r.pending; count = r.count; await load(); return true }
    catch (e) { error = e.message; return false } finally { saving = false }
  }
  async function apply() {
    applying = true; error = ''; notice = ''
    try {
      const r = await api.apply()
      notice = r.servant === 'healthy'
        ? 'Applied — proxy restarted and healthy.'
        : `Applied, but the proxy is unhealthy: ${r.detail || ''} — fix the setting and re-Apply.`
      await load(); return true
    } catch (e) { error = e.status === 422 ? `Invalid config: ${e.message}` : e.message; await load(); return false }
    finally { applying = false }
  }
  async function discard(kind, name) {
    saving = true; error = ''; notice = ''
    try {
      const q = kind && name ? `?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}` : ''
      await api.discard(q); await load(); notice = 'Discarded staged changes.'; return true
    } catch (e) { error = e.message; await load(); return false } finally { saving = false }
  }
  return {
    get items(){return items}, get loading(){return loading}, get saving(){return saving},
    get applying(){return applying}, get error(){return error}, get notice(){return notice},
    get pending(){return pending}, get count(){return count},
    load, itemsOfKind, itemNamed, stageItem, deleteItem, apply, discard,
  }
}
```
NOTE: in Step 1, update `api.discard` to accept an optional query suffix: `discard: (q = '') => req('/api/discard' + q, { method: 'POST' })`.

- [ ] **Step 3: build** `cd ui/frontend && npm run build` will FAIL (screens still use the old store) — that's expected; the screens are rewired in T3–T7. **To keep an intermediate green build, do T1 then immediately T2–T7 before building, OR** temporarily comment nothing and accept the build is green only after T7. Recommended: commit T1 without a standalone build; build at T2.

- [ ] **Step 4: commit** `git add ui/frontend/src/lib/api.js ui/frontend/src/lib/configStore.svelte.js && git commit -m "feat(ui): item-model config store + /api/config/* helpers (v3)"`

---

## Task 2: App.svelte — Apply/Discard bar (item-model) + mount load

**Files:** Modify `ui/frontend/src/App.svelte`. **READ it first** (it has the existing apply bar + the shared `store`).

- [ ] **Step 1:** ensure the shared store is `createConfigStore()` and `store.load()` runs on mount (replace any `store.refreshPending()`/`store.config` usage). The Apply bar (shown when `store.pending`):
```svelte
    {#if store.pending}
      <div class="applybar">
        <span><strong>{store.count}</strong> unapplied change{store.count === 1 ? '' : 's'}</span>
        <div class="applybar-actions">
          <button class="discard" onclick={confirmDiscard} disabled={store.saving || store.applying}>Discard all</button>
          <button class="apply" onclick={() => store.apply()} disabled={store.applying || store.saving}>{store.applying ? 'Applying… (~25s)' : 'Apply'}</button>
        </div>
      </div>
    {/if}
    {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
    {#if store.error}<div class="banner err">{store.error}</div>{/if}
```
`confirmDiscard = () => { if (confirm(`Discard all ${store.count} unapplied change(s)? Reverts to the last applied config.`)) store.discard() }`. Keep the existing `.applybar`/`.apply`/`.discard` styles. Config screens receive `{store}`; Models/Routing/Caching/ProviderKeys re-render off `store.items` (so they refresh after stage/apply/discard).

- [ ] **Step 2: build** — will still fail until screens are rewired; proceed to T3–T7 then build. (Or build after T7.)
- [ ] **Step 3: commit** `feat(ui): App apply/discard bar on item-model store`.

---

## Task 3: Routing.svelte → router_setting items

**Files:** Rewrite `ui/frontend/src/routes/Routing.svelte`. The router settings are `kind='router_setting'` items, name = the key (`routing_strategy`, `num_retries`, `timeout`, `cooldown_time`, `allowed_fails`, `retry_after`, `fallbacks`).

- [ ] **Step 1:** read each field from `store.itemNamed('router_setting', key)?.data` (with the field's `flag`); on Save, `store.stageItem('router_setting', key, value)` per changed field (or one item per field). Show a per-field flag indicator (accent dot if `flag==='new'||'changed'`). `routing_strategy` keeps the valid-enum `<select>`; numeric fields as before; `fallbacks` as JSON textarea. Each field's Save stages that item. (The global Apply bar applies.)
- [ ] **Step 2: build** + (after all screens) commit. (Group the screen commits or commit per screen once the build is green at T7.)

---

## Task 4: Caching.svelte → litellm_setting cache items (read-only display)

**Files:** Rewrite `ui/frontend/src/routes/Caching.svelte`. Caching is `litellm_setting` items `cache` (bool) + `cache_params` (dict). Per the v2.1 decision, keep it **read-only**: show `cache` on/off, `cache_params.type`, the effective `valkey:6379` (the `cache_params.host/port` are `os.environ/` refs from compose), and a note that the backend is provisioned in docker-compose. Read from `store.itemNamed('litellm_setting','cache')?.data` and `...'cache_params'`. No staging here.

- [ ] **Step 1:** implement the read-only panel from the items. **Step 2:** build/commit at T7.

---

## Task 5: Models.svelte → model items + catalog picker + credential dropdown

**Files:** Rewrite `ui/frontend/src/routes/Models.svelte`. Models are `kind='model'` items (data `{litellm_params, model_info}`).

- [ ] **Step 1:** list `store.itemsOfKind('model')` in the table; each row shows its flag — `new`/`changed` accent, **`deleted` struck-through** (still listed until Apply/Discard) with an "undo" that calls `store.discard('model', name)`. Health dot from `api.modelsHealth()` (unchanged). Add-model form: the v2.4 **catalog provider picker** (datalist from `api.catalogProviders()`, prefix affix, mode filter, advanced api_base, special fields) — keep it; the credential dropdown reads `store.itemsOfKind('credential')` (names). On Save: `store.stageItem('model', form.modelName, { litellm_params: buildParams(), model_info: { mode } })`. On delete: `store.deleteItem('model', name)`. Keep Test connection (`api.testModel`) + cost fields + catalog pricing auto-fill.
- [ ] **Step 2:** build/commit at T7.

---

## Task 6: ProviderKeys.svelte → credential items (flag + strikethrough)

**Files:** Rewrite `ui/frontend/src/routes/ProviderKeys.svelte`. Credentials are `kind='credential'` items (`data:{provider, api_key:"***"}` from state).

- [ ] **Step 1:** list `store.itemsOfKind('credential')` (name + provider; value always `***`); each row shows its flag — `new`/`changed` accent, **`deleted` red + strikethrough** with an undo (`store.discard('credential', name)`). Add-key form (name + provider [catalog list] + key): on Save `store.stageItem('credential', name, { provider, api_key })` (plaintext key → backend encrypts). Delete → `store.deleteItem('credential', name)` (stages a `deleted`, shown struck-through until Apply). The hint: "encrypted at rest; written into config.yaml on Apply; Discard reverts staged changes."
- [ ] **Step 2:** build/commit at T7.

---

## Task 7: Settings passthrough editor + ConfigViewer preview + Dashboard count; BUILD GREEN

**Files:** Modify `ui/frontend/src/routes/Settings.svelte`, `ConfigViewer.svelte`, `Dashboard.svelte`. Then the full build must pass.

- [ ] **Step 1: Settings** — add a **Raw / advanced (passthrough)** card: load `api.passthroughGet()` → a `<textarea>` bound to its `yaml`; "Save passthrough" → `store ? store.load() : ...`; actually call `api.passthroughPut(yaml)` then `store.load()` (stages it → Apply bar). Show parse errors (422 detail). Keep the existing catalog-sync panel + dark mode + export.
- [ ] **Step 2: ConfigViewer** — replace its content with the **rendered preview**: `api.configRendered()` → pretty-print `{config}` (YAML or JSON) read-only, with a note "this is what will be written to config.yaml on Apply (secrets redacted)".
- [ ] **Step 3: Dashboard** — replace `api.config()` usage: model count = `store.itemsOfKind('model').length` (ensure `store.load()` ran) or call `api.configState()`; cache card from the `litellm_setting` cache item. Keep health/usage/keys cards.
- [ ] **Step 4: BUILD** `cd ui/frontend && npm run build` → **must succeed now** (all consumers rewired). Fix any remaining references to the removed store methods (`store.config`, `saveSection`, `refreshPending`, `api.config`, `api.credentials`, etc.) — grep: `grep -rn "store.config\|saveSection\|refreshPending\|api.config\b\|api.putConfig\|api.credentials\|api.cacheInfo\|applyStatus" src`.
- [ ] **Step 5: commit** all rewired screens: `git add ui/frontend/src && git commit -m "feat(ui): rewire all config screens to the item model + flag rendering + passthrough editor + rendered preview"`.

---

## Task 8: dead-code cleanup (backend)

**Files:** `ui/app/apply.py`, `ui/app/config_store.py`, tests. Grep-driven — remove only confirmed-dead code.

- [ ] **Step 1:** `grep -rn "from app.apply\|import apply\b\|safe_apply\|seed_baseline_if_missing\|promote_baseline\|restore_baseline\|pending_status\|\.applied.yaml" ui/app ui/tests` — identify v1/v2 file-diff helpers no longer referenced by v3 (`config_engine`/`config_v3_routes` use `config_store.write_config_atomic`/`validate_config`/`ConfigError`/`load_config` only).
- [ ] **Step 2:** delete `ui/app/apply.py` + `ui/tests/test_apply.py` if unreferenced; remove the `.applied.yaml` baseline helpers (`seed_baseline_if_missing`/`promote_baseline`/`restore_baseline`/`pending_status`) from `config_store.py` + their tests, IF nothing imports them (the v3 engine doesn't). Keep `write_config`/`write_config_atomic`/`validate_config`/`load_config`/`load`/`ProxyConfig`/secret-guardrails. Update `.gitignore` note if `.applied.yaml` is gone (harmless to leave).
- [ ] **Step 3:** run full backend suite → green (drop the deleted tests). **Step 4: commit** `refactor(ui): remove dead v1/v2 file-diff apply helpers (superseded by config_engine)`.

---

## Task 9: real-stack integration verification (Playwright)

- [ ] **Step 1:** local-build override; `docker compose up -d --build --wait`; log in (admin pw). Trigger a catalog sync (Settings) so the provider picker is populated.
- [ ] **Step 2 — flag rendering + stage:** Routing → change strategy + Save → the field shows a `changed` accent + the Apply bar shows "1 unapplied change". Models → add a model → it appears with a `new` accent. ProviderKeys → add a key → `new`; delete an *applied* item → it shows red **strikethrough** (still listed), Apply bar count increments.
- [ ] **Step 3 — Discard:** per-item undo on a struck-through delete restores it (flag clears); "Discard all" clears the bar (state reverts).
- [ ] **Step 4 — Apply:** Apply → one restart → bar clears; ConfigViewer "rendered preview" matches; `/v1/models` shows the applied models; a credential is `***` in the UI/preview but literal in `config.yaml` (container cat).
- [ ] **Step 5 — passthrough:** Settings → add `callbacks:\n  - langfuse` → Apply → preview + `config.yaml` show it.
- [ ] **Step 6 — capture screenshots** of the rewired screens (dark mode) into `docs/images/` for the README refresh; tear down; restore config; `git status` clean.

## Self-Review
- **Spec coverage:** item-model store (T1) ✓; Apply/Discard bar from DB pending (T2) ✓; every config screen → state/item with flag rendering + strikethrough deletes + per-item undo (T3–T6) ✓; passthrough editor (T7) ✓; rendered preview (T7) ✓; dead-code cleanup (T8) ✓; integration incl. flags/strikethrough/discard/passthrough (T9) ✓.
- **Placeholders:** the per-screen tasks describe the rewiring concretely + reference the verified endpoints + the existing screens (implementer reads each); the store (T1) has full code. Build-green gate is explicit at T7 Step 4 with the grep.
- **Type consistency:** `store.{items,itemsOfKind,itemNamed,stageItem,deleteItem,apply,discard,pending,count}`; `api.{configState,stageItem,deleteItem,configRendered,passthroughGet,passthroughPut,discard(q)}`; item `{kind,name,data,flag}` consistent.

## Follow-on (post-v3.3, before merge)
- Docs refresh (README/admin-ui) for the v3 master/servant model + new screenshots.
- Merge `v3-master-servant` → `main` (one release) per `finishing-a-development-branch`.
