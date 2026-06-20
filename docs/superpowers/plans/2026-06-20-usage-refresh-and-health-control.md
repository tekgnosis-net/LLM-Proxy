# Usage Auto-Refresh In-Place + Per-Model Health Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Usage dashboard auto-refresh update in place (no scroll reset), and give each model a Health control (disable billed background checks + on-demand "Check now"), plus an editable global health-check interval.

**Architecture:** Frontend-only changes plus zero new backend code — the "Check now" path reuses the existing `POST /api/models/test` (→ LiteLLM `/health/test_connection`), and the per-model disable flag and global interval ride the existing `ui_config` staging machinery as `model_info.disable_background_health_check`, `general_settings.health_check_skip_disabled_background_models`, and `litellm_settings.health_check_interval` items. This is the `1.20.0` slice of the hybrid-hot-apply spec — independently shippable, no keystone risk.

**Tech Stack:** Svelte 5 (runes: `$state`/`$derived`/`$effect`), FastAPI (untouched here), the existing `ui_config` staging store, Vite build, Playwright for integration verification.

## Global Constraints

- This is the source spec: `docs/superpowers/specs/2026-06-20-hybrid-hot-apply-design.md` (§4 health, §8 phasing). Implement only the `1.20.0` scope: **auto-refresh in-place** and **per-model health control**. Do NOT start the hybrid engine (that's the separate `1.21.0` plan).
- The frontend has **no unit-test harness** (confirmed: only `ui/tests/*.py` exist). Frontend tasks are verified by `cd ui/frontend && npm run build` (compile gate) **and** Playwright against the LAN-IP preview, per the established pattern. Backend stays untouched, so its 200+ pytest suite must remain green.
- **Verify the UI via the LAN IP `http://10.0.20.85:8081`, never localhost** (served plain-http on a LAN IP = non-secure context; localhost masks that). Local stack admin password: `Smoke-Admin-2026`.
- Do not commit `.env`. Do not push or release — the human merges and cuts the version.
- Match existing Svelte style: `$state` for mutable UI state, `bind:` for inputs, `api.*` for calls, `store.stageItem(kind,name,data)` / `store.itemsOfKind(kind)` for config items. Keep the existing class names / CSS vocabulary.

---

## File Structure

- `ui/frontend/src/routes/Usage.svelte` — Task 1. The `load()` function gains a `silent` parameter; auto-refresh ticks call it silently.
- `ui/frontend/src/routes/Models.svelte` — Tasks 2 & 3. Add a Health-check toggle to the add/edit form (writes `model_info.disable_background_health_check`, stages the one-time global skip flag) and a per-row "Check now" button.
- `ui/frontend/src/routes/Settings.svelte` — Task 4. Add a "Health checks" card with an editable global interval (`litellm_settings.health_check_interval`).
- Task 5 touches no source files — it is the integration-verification + release-prep gate.

No backend files change in this plan.

---

### Task 1: Usage auto-refresh updates in place (no scroll reset)

**Files:**
- Modify: `ui/frontend/src/routes/Usage.svelte:18-36`

**Interfaces:**
- Produces: `load(silent = false)` — when `silent` is true, the fetch does NOT clear `summary`/`recent` or toggle `loading`, so Svelte patches the existing DOM in place instead of unmounting the dashboard.
- Consumes: nothing new.

**Root cause (from the spec):** `load()` unconditionally runs `loading = true; summary = null; recent = []` on every call, and `arm()` registers `load` as the `setInterval` callback. So every auto-refresh tick blanks `summary`, the `{#if loading}…{:else if summary}` block unmounts the whole dashboard, and the browser resets scroll to the top.

- [ ] **Step 1: Add the `silent` parameter to `load()`**

Replace the current `load` (lines 18-29) with:

```js
  async function load(silent = false) {
    if (!silent) { loading = true; summary = null; recent = [] }
    err = ''
    try {
      const [d, rec] = await Promise.all([
        api.get(`/api/usage/summary?days=${days}`),
        api.get('/api/usage/recent?limit=50')
      ])
      summary = d
      recent = rec.recent ?? []
    } catch (e) { err = e.message }
    finally { if (!silent) loading = false }
  }
```

- [ ] **Step 2: Make the auto-refresh tick silent**

In `arm()` (lines 33-36), change the interval callback from `load` to `() => load(true)`:

```js
  function arm() {
    if (timer) { clearInterval(timer); timer = null }
    if (refreshSec > 0 && !document.hidden) timer = setInterval(() => load(true), refreshSec * 1000)
  }
```

Leave the two `$effect`s unchanged: the `days` effect still calls `load()` (full spinner on range change / first mount); only the interval is silent.

- [ ] **Step 3: Compile gate**

Run: `cd ui/frontend && npm run build`
Expected: build succeeds with no errors (Svelte compiles `Usage.svelte`).

- [ ] **Step 4: Verify in place via Playwright (LAN IP)**

Bring up the local stack if not running, then with Playwright against `http://10.0.20.85:8081` (login `Smoke-Admin-2026`):
1. Navigate to Usage, set Auto-refresh to `10s`, set range to `30d`.
2. Scroll to the Recent-activity table (bottom of the page) and record `window.scrollY` via `browser_evaluate(() => window.scrollY)`.
3. Wait ~12s for one auto-refresh tick (`browser_wait_for`).
4. Read `window.scrollY` again.

Expected: the second `scrollY` equals the first (no jump to top), the "Loading…" placeholder never appears during the tick, and KPI/table cells reflect refreshed data. Capture a screenshot for the release notes.

- [ ] **Step 5: Commit**

```bash
git add ui/frontend/src/routes/Usage.svelte
git commit -m "fix(ui): usage auto-refresh updates in place — no scroll reset (silent load)"
```

---

### Task 2: Per-model Health-check toggle (disable billed background checks)

**Files:**
- Modify: `ui/frontend/src/routes/Models.svelte` — form state (`:12`, `:59`), `editModel` (`:70-83`), `saveModel` (`:138-153`), the add/edit form markup (after the Mode block, `:227`).

**Interfaces:**
- Produces: model items whose `data.model_info.disable_background_health_check` is `true` when the toggle is on; and a one-time `general_setting` item `health_check_skip_disabled_background_models = true` staged the first time any model disables its check.
- Consumes: `store.stageItem(kind,name,data)`, `store.itemsOfKind('general_setting')` (existing).

**Why a global flag too:** LiteLLM only honors per-model `disable_background_health_check` when `general_settings.health_check_skip_disabled_background_models: true` is also set. Staging it once (a `general_setting` → config.yaml → one restart on Apply) makes every subsequent toggle take effect; the spec documents this one-time cost.

- [ ] **Step 1: Add `disableHealthCheck` to the form initial state**

In the `form = $state({...})` initializer (line 12) and the identical object in `resetForm()` (line 59), add `disableHealthCheck: false` as the last field. For line 12:

```js
  let form = $state({ modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '', vertex_project: '', vertex_location: '', credential: '', mode: 'chat', input_cost: '', output_cost: '', disableHealthCheck: false })
```

Apply the same `, disableHealthCheck: false` addition to the object literal inside `resetForm()` (line 59).

- [ ] **Step 2: Load the flag when editing a model**

In `editModel()` (lines 75-80), add `disableHealthCheck` to the `form = {…}` assignment, reading it from the item's `model_info`:

```js
    form = { modelName: d.model_name, modelId: slash > 0 ? full.slice(slash+1) : full,
      api_key_env: '', api_base: lp.api_base || '', api_version: lp.api_version || '',
      aws_region_name: lp.aws_region_name || '', vertex_project: lp.vertex_project || '', vertex_location: lp.vertex_location || '',
      credential: lp.litellm_credential_name || '', mode: (d.model_info||{}).mode || 'chat',
      input_cost: lp.input_cost_per_token!=null ? perTokenToPerM(lp.input_cost_per_token) : '',
      output_cost: lp.output_cost_per_token!=null ? perTokenToPerM(lp.output_cost_per_token) : '',
      disableHealthCheck: !!((d.model_info||{}).disable_background_health_check) }
```

- [ ] **Step 3: Add the `ensureHealthSkipFlag` helper**

Add this function next to `saveModel` (e.g. after line 153):

```js
  // LiteLLM only honors per-model disable_background_health_check when this global
  // flag is set. Stage it once (idempotent) the first time any model disables its check.
  async function ensureHealthSkipFlag() {
    const exists = store.itemsOfKind('general_setting')
      .some(i => i.name === 'health_check_skip_disabled_background_models')
    if (!exists) await store.stageItem('general_setting', 'health_check_skip_disabled_background_models', true)
  }
```

- [ ] **Step 4: Write the flag on save**

In `saveModel()` (lines 146-152), build `model_info` with the flag and stage the global skip when disabling:

```js
    const id = editingId || uuidv4()
    const mi = { mode: form.mode }
    if (form.disableHealthCheck) mi.disable_background_health_check = true
    const ok = await store.stageItem('model', id, {
      model_name: form.modelName,
      litellm_params: buildParams(),
      model_info: mi
    })
    if (ok && form.disableHealthCheck) await ensureHealthSkipFlag()
    if (ok) resetForm()   // keep the user's input on a rejected save (422)
```

- [ ] **Step 5: Add the toggle to the form markup**

Immediately after the Mode `</label>` block (after line 227, before the Advanced button on line 230), insert:

```svelte
      <label class="check"><input type="checkbox" bind:checked={form.disableHealthCheck} />
        Disable background health check
        <span class="hint">Recommended for paid providers (e.g. deepinfra) — the background check sends a real billed request on each interval. Use “Check now” on demand instead.</span>
      </label>
```

Add a matching style rule inside the `<style>` block (next to `.hint`, around line 326):

```css
  label.check{flex-direction:row;align-items:flex-start;gap:8px;flex-wrap:wrap}
  label.check input{margin-top:2px}
```

- [ ] **Step 6: Compile gate**

Run: `cd ui/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 7: Verify via Playwright (LAN IP)**

On `http://10.0.20.85:8081`:
1. Edit an existing model, tick "Disable background health check", Save.
2. Re-open the same model in Edit — assert the checkbox is still ticked (round-trips through `model_info`).
3. Open the Config/Review screen (or `GET /api/config/rendered`) — assert the staged general_setting `health_check_skip_disabled_background_models: true` is present once (not duplicated after a second disabled model).

Expected: flag persists on the model; global skip staged exactly once.

- [ ] **Step 8: Commit**

```bash
git add ui/frontend/src/routes/Models.svelte
git commit -m "feat(ui): per-model health-check disable toggle (+ one-time global skip flag)"
```

---

### Task 3: Per-model "Check now" button (on-demand health)

**Files:**
- Modify: `ui/frontend/src/routes/Models.svelte` — add a handler near `healthInfo` (`:163`) and a button + result cell in the model-list row (`:293-300`), plus the table header (`:269`).

**Interfaces:**
- Consumes: `api.testModel({ litellm_params, mode })` (existing — `POST /api/models/test` → LiteLLM `/health/test_connection`).
- Produces: in-row, on-demand pass/fail feedback. No new state crosses task boundaries.

**Why reuse `testModel`:** the spec's on-demand check is exactly the existing connection test (`/health/test_connection` with the model's own `litellm_params`). Operator-initiated, so its cost is intentional — no recurring billing.

- [ ] **Step 1: Add the `checkNow` handler + result state**

After `healthInfo()` (line 169), add:

```js
  let checkResult = $state({})   // item.name → { busy?:bool, ok?:bool, msg?:string }
  async function checkNow(item) {
    const lp = item.data?.litellm_params || {}
    const mode = (item.data?.model_info || {}).mode || 'chat'
    checkResult = { ...checkResult, [item.name]: { busy: true } }
    try {
      const r = await api.testModel({ litellm_params: lp, mode })
      const ok = r.status === 'success'
      checkResult = { ...checkResult, [item.name]: { ok, msg: ok ? 'OK' : 'Failed' } }
    } catch (e) {
      checkResult = { ...checkResult, [item.name]: { ok: false, msg: e.message } }
    }
  }
```

- [ ] **Step 2: Add a "Check" column header**

In the table `<thead>` (line 269), add a header before the trailing empty `<th></th>`:

```svelte
        <thead><tr><th>Model name</th><th>litellm model</th><th>Costs</th><th>Health</th><th>Check</th><th>Status</th><th></th></tr></thead>
```

- [ ] **Step 3: Add the Check-now cell to each row**

Insert a new `<td>` immediately after the Health dot cell (after line 286, before the Status `<td>` on line 287):

```svelte
              <td>
                {#if flag !== 'deleted'}
                  {@const cr = checkResult[item.name]}
                  <button onclick={() => checkNow(item)} disabled={cr?.busy} title="Run an on-demand health check now">
                    {cr?.busy ? '…' : 'Check now'}
                  </button>
                  {#if cr && !cr.busy}
                    <span class="check-res" class:ok={cr.ok} class:bad={!cr.ok} title={cr.msg}>{cr.ok ? '✓' : '✗'}</span>
                  {/if}
                {/if}
              </td>
```

- [ ] **Step 4: Add result styling**

Inside `<style>` (near `.dot`, line 327) add:

```css
  .check-res{margin-left:6px;font-weight:600}
  .check-res.ok{color:#34c759}
  .check-res.bad{color:#ff3b30}
```

- [ ] **Step 5: Compile gate**

Run: `cd ui/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Verify via Playwright (LAN IP)**

On `http://10.0.20.85:8081` → Models: click "Check now" on a healthy local model → expect a `✓` within a few seconds; click it on a model with a bad base URL → expect `✗` with the error in the tooltip.

- [ ] **Step 7: Commit**

```bash
git add ui/frontend/src/routes/Models.svelte
git commit -m "feat(ui): per-model 'Check now' on-demand health button"
```

---

### Task 4: Editable global health-check interval (Settings)

**Files:**
- Modify: `ui/frontend/src/routes/Settings.svelte` — add a "Health checks" card (after the passthrough card, around line 65) and its script state.

**Interfaces:**
- Consumes: `store.itemsOfKind('litellm_setting')`, `store.stageItem('litellm_setting', 'health_check_interval', <int>)` (existing).
- Produces: a staged `litellm_setting` `health_check_interval` (seconds). Applies on the next Apply (a settings change → restart, by design).

**Why here, not Models:** LiteLLM's interval is global (no per-model interval exists — spec §4/§9). Lengthening it spaces out checks for the free/local models that stay enabled.

- [ ] **Step 1: Add interval state + loader**

In the `<script>` (after the passthrough state, ~line 35), add:

```js
  // Global health-check interval (litellm_settings.health_check_interval, seconds)
  let hcInterval = $state('')
  let hcMsg = $state(''), hcErr = $state(''), hcBusy = $state(false)
  function loadHcInterval() {
    const it = store.itemsOfKind('litellm_setting').find(i => i.name === 'health_check_interval')
    hcInterval = (it && it.data != null) ? String(it.data) : ''
  }
  async function saveHcInterval() {
    hcBusy = true; hcMsg = ''; hcErr = ''
    try {
      const n = parseInt(hcInterval, 10)
      if (!Number.isFinite(n) || n < 30) { hcErr = 'Enter a whole number of seconds ≥ 30'; return }
      await store.stageItem('litellm_setting', 'health_check_interval', n)
      hcMsg = 'Staged. Click Apply to make it live (settings change → brief restart).'
    } catch (e) { hcErr = e.message }
    finally { hcBusy = false }
  }
```

Call `loadHcInterval()` from the existing `onMount` (line 38-41), alongside `loadPassthrough()`:

```js
  onMount(() => {
    api.catalogStatus().then(s => catStatus = s).catch(() => {})
    loadPassthrough()
    loadHcInterval()
  })
```

- [ ] **Step 2: Add the Health-checks card markup**

Insert after the passthrough card's closing `</div>` (after line 65), before the Export card:

```svelte
  <div class="card"><h2>Health checks</h2>
    <p class="hint">How often LiteLLM runs background health checks (seconds), for models that keep them enabled. Per-model checks are toggled on the Models screen; paid providers should disable theirs to avoid recurring billed probes.</p>
    <label class="hc">Interval (seconds)
      <input type="number" min="30" step="30" bind:value={hcInterval} placeholder="e.g. 300" />
    </label>
    <div class="row" style="margin-top:8px">
      <button onclick={saveHcInterval} disabled={hcBusy}>{hcBusy ? 'Saving…' : 'Save interval'}</button>
    </div>
    {#if hcErr}<div class="banner err">{hcErr}</div>{/if}
    {#if hcMsg}<div class="banner ok">{hcMsg}</div>{/if}
  </div>
```

Add a style rule next to `.pw-fields` (line 104):

```css
  label.hc{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text);max-width:220px}
```

- [ ] **Step 3: Compile gate**

Run: `cd ui/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Verify via Playwright (LAN IP)**

On `http://10.0.20.85:8081` → Settings: enter `300` → Save interval → expect the "Staged" message; open `GET /api/config/rendered` (or the Review screen) → assert `litellm_settings.health_check_interval: 300`. Enter `5` → Save → expect the validation error and nothing staged.

- [ ] **Step 5: Commit**

```bash
git add ui/frontend/src/routes/Settings.svelte
git commit -m "feat(ui): editable global health_check_interval in Settings"
```

---

### Task 5: Integration sweep + release prep

**Files:** none modified — this is the verification + handoff gate.

- [ ] **Step 1: Backend suite stays green**

Run: `cd ui && python -m pytest tests/ -q`
Expected: same pass count as before this plan (no backend files changed; nothing regressed).

- [ ] **Step 2: Production build**

Run: `cd ui/frontend && npm run build`
Expected: clean build (this is what the container image ships).

- [ ] **Step 3: Full Playwright pass (LAN IP)**

On `http://10.0.20.85:8081`, in one session confirm all four behaviors together: (a) Usage auto-refresh holds scroll position over a tick; (b) a model's health toggle persists and stages the global skip once; (c) "Check now" returns ✓/✗; (d) the global interval stages and renders. Capture screenshots for the release notes.

- [ ] **Step 4: Update the live-stack note (optional, no secrets)**

If the release notes / changelog file is updated as part of this repo's flow, record `1.20.0`: "Usage auto-refresh in place (no scroll reset); per-model health-check disable + on-demand Check now; editable global health-check interval." Do NOT push or tag — the human merges to `main` (which cuts the version + image) and pins it.

- [ ] **Step 5: Hand off**

Report: branch ready, all checks green, screenshots attached. The human merges → semantic-release cuts `1.20.0` and the `llm-proxy-ui` image; the human bumps the compose pin.

---

## Self-Review

**Spec coverage (§4 health, §8 phasing item 1-2, §11 frontend tests):**
- Auto-refresh in-place (§8.1, §11 "Usage auto-refresh") → Task 1. ✓
- Per-model disable via `disable_background_health_check` + one-time global skip (§4) → Task 2. ✓
- On-demand "Check now" reusing `test_connection` (§4) → Task 3. ✓
- Editable global `health_check_interval` (§4) → Task 4. ✓
- Integration verification on LAN IP (§11 frontend) → Task 5. ✓
- The hybrid engine / export (§2-3, §6) is **intentionally excluded** — separate `1.21.0` plan.

**Placeholder scan:** No TBDs; every code step shows exact code and exact insertion points (line anchors). Frontend "tests" are Playwright assertions (the codebase has no frontend unit harness) — stated explicitly in Global Constraints.

**Type/name consistency:** `disableHealthCheck` (form field) ↔ `disable_background_health_check` (model_info key) used consistently in Tasks 2; `ensureHealthSkipFlag` defined and called in Task 2; `checkNow`/`checkResult` defined and used in Task 3; `health_check_interval` (litellm_setting) consistent in Task 4; `load(silent)` signature consistent in Task 1. `store.itemsOfKind` / `store.stageItem` match the existing `api.js`/store surface read from the codebase.
