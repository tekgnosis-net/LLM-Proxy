# LLM-Proxy Admin UI — Phase 2 (UI: Models + Routing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Frontend (Svelte 5) has no unit-test harness in this repo, so verification is **build + real-stack integration** (the backend is already TDD'd to 41 tests). Steps use `- [ ]`.

**Goal:** Add the **Models** and **Routing** screens to the Svelte SPA, editing `config.yaml` through the safe-apply backend (`GET /api/config` → merge → `PUT /api/config`). Every save round-trips the **full** config (never partial — a partial PUT drops keys like `general_settings.master_key`, the P2.7 finding).

**Architecture:** A small `configStore` (Svelte 5 runes) loads the full config once and is the single in-memory copy. Models/Routing screens mutate their section of that copy; **Save & apply** PUTs the whole object and surfaces 200/422/409 + the ~25s "restarting proxy" state. Apple-HIG styling consistent with the Phase 1 shell + prototype.

**Tech Stack:** Svelte 5 (runes, `mount`), existing `frontend/` Vite app. Backend unchanged.

**Spec/visual:** [`../specs/2026-06-07-llm-proxy-ui-design.md`](../specs/2026-06-07-llm-proxy-ui-design.md) + [`../specs/2026-06-07-llm-proxy-ui-prototype.html`](../specs/2026-06-07-llm-proxy-ui-prototype.html) · **Schema:** [`../../config-schema.md`](../../config-schema.md)

---

## File Structure

```
ui/frontend/src/
├── lib/
│   ├── api.js          # MODIFY: add putConfig()
│   ├── configStore.svelte.js  # CREATE: load/hold/save full config (runes)
│   └── providers.js    # CREATE: provider presets (model prefix, fields, env var)
├── routes/
│   ├── Models.svelte   # CREATE: model_list CRUD
│   └── Routing.svelte  # CREATE: router_settings editor
└── App.svelte          # MODIFY: add Models + Routing to sidebar nav
```

---

## Task 1: api + configStore + provider presets

**Files:** Modify `ui/frontend/src/lib/api.js`; Create `ui/frontend/src/lib/configStore.svelte.js`, `ui/frontend/src/lib/providers.js`.

- [ ] **Step 1: add `putConfig` to `api.js`** — alongside the existing `req`/`api` object:
```javascript
  // in the api object:
  putConfig: (config) => req('/api/config', { method: 'PUT', body: JSON.stringify(config) }),
```
Also make `req` surface the HTTP status on error so the UI can distinguish 422 vs 409:
```javascript
async function req(path, opts = {}) {
  const r = await fetch(path, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...opts })
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail || r.statusText
    const err = new Error(detail); err.status = r.status; throw err
  }
  return r.json()
}
```

- [ ] **Step 2: create `ui/frontend/src/lib/providers.js`** — presets driving the Add-Model form:
```javascript
// Provider presets: how to build litellm_params.model + which fields/secret env var.
export const PROVIDERS = [
  { id: 'openai',     label: 'OpenAI',            prefix: 'openai/',     keyEnv: 'OPENAI_API_KEY',    fields: ['api_key'] },
  { id: 'anthropic',  label: 'Anthropic',         prefix: 'anthropic/',  keyEnv: 'ANTHROPIC_API_KEY', fields: ['api_key'] },
  { id: 'azure',      label: 'Azure OpenAI',      prefix: 'azure/',      keyEnv: 'AZURE_API_KEY',     fields: ['api_key', 'api_base', 'api_version'] },
  { id: 'gemini',     label: 'Google Gemini',     prefix: 'gemini/',     keyEnv: 'GEMINI_API_KEY',    fields: ['api_key'] },
  { id: 'bedrock',    label: 'AWS Bedrock',       prefix: 'bedrock/',    keyEnv: null,                fields: ['aws_region_name'] },
  { id: 'openai_compat', label: 'OpenAI-compatible / local (vLLM, Ollama)', prefix: 'openai/', keyEnv: null, fields: ['api_base', 'api_key'], customProvider: 'openai' },
]
// Secrets are emitted as os.environ/<VAR>, never literals (config.yaml has no secrets).
export function buildLitellmParams(provider, form) {
  const p = { model: provider.prefix + form.modelId }
  if (provider.customProvider) p.custom_llm_provider = provider.customProvider
  if (form.api_base) p.api_base = form.api_base
  if (form.api_version) p.api_version = form.api_version
  if (form.aws_region_name) p.aws_region_name = form.aws_region_name
  // api_key: store as an env reference the operator sets in .env (never the literal)
  if (provider.fields.includes('api_key') && form.api_key_env) p.api_key = `os.environ/${form.api_key_env}`
  return p
}
```

- [ ] **Step 3: create `ui/frontend/src/lib/configStore.svelte.js`** — the single in-memory full config + save:
```javascript
import { api } from './api.js'

export function createConfigStore() {
  let config = $state(null)      // the full config object (source of truth in memory)
  let loading = $state(false)
  let applying = $state(false)   // true during the ~25s PUT (proxy restart)
  let error = $state('')
  let notice = $state('')

  async function load() {
    loading = true; error = ''
    try { config = await api.config() } catch (e) { error = e.message } finally { loading = false }
  }
  // Replace one top-level section then PUT the FULL config (never partial).
  async function saveSection(section, value) {
    if (!config) return
    const candidate = { ...config, [section]: value }
    applying = true; error = ''; notice = ''
    try {
      const res = await api.putConfig(candidate)
      config = candidate
      notice = `Applied — ${(res.models || []).length} model(s), routing: ${res.routing_strategy || '—'}`
      return true
    } catch (e) {
      if (e.status === 422) error = `Rejected (not applied): ${e.message}`
      else if (e.status === 409) error = `Reload failed — rolled back to the previous config: ${e.message}`
      else error = e.message
      return false
    } finally { applying = false }
  }
  return {
    get config() { return config }, get loading() { return loading },
    get applying() { return applying }, get error() { return error }, get notice() { return notice },
    load, saveSection,
  }
}
```

- [ ] **Step 4: build** `cd ui/frontend && npm run build` → expect success (these are importable modules; no UI wired yet). Commit:
```bash
git add ui/frontend/src/lib/
git commit -m "feat(ui): config store + provider presets + putConfig (full round-trip)"
```

---

## Task 2: Models screen

**Files:** Create `ui/frontend/src/routes/Models.svelte`.

- [ ] **Step 1: implement `Models.svelte`** — list `config.model_list`, add via a provider-driven form, edit/delete, then **Save & apply** (saveSection('model_list', ...)). Full component:
```svelte
<script>
  import { onMount } from 'svelte'
  import { PROVIDERS, buildLitellmParams } from '../lib/providers.js'
  let { store } = $props()
  let showAdd = $state(false)
  let provider = $state(PROVIDERS[0])
  let form = $state({ modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '' })
  onMount(() => { if (!store.config) store.load() })

  function models() { return store.config?.model_list ?? [] }
  function resetForm() { form = { modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '' }; provider = PROVIDERS[0]; showAdd = false }
  async function addModel() {
    const entry = { model_name: form.modelName, litellm_params: buildLitellmParams(provider, form) }
    await store.saveSection('model_list', [...models(), entry])
    resetForm()
  }
  async function deleteModel(i) {
    await store.saveSection('model_list', models().filter((_, j) => j !== i))
  }
</script>

<div class="page">
  <header><h1>Models</h1>
    <button class="primary" onclick={() => showAdd = !showAdd} disabled={store.applying}>＋ Add model</button>
  </header>
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}

  {#if showAdd}
    <div class="card add">
      <label>Provider
        <select bind:value={provider}>{#each PROVIDERS as p}<option value={p}>{p.label}</option>{/each}</select>
      </label>
      <label>Public model name <input bind:value={form.modelName} placeholder="e.g. gpt-4o" /></label>
      <label>Provider model id <input bind:value={form.modelId} placeholder="e.g. gpt-4o (→ {provider.prefix}…)" /></label>
      {#if provider.fields.includes('api_key')}
        <label>API key env var <input bind:value={form.api_key_env} placeholder={provider.keyEnv || 'MY_API_KEY'} /></label>
      {/if}
      {#if provider.fields.includes('api_base')}<label>API base <input bind:value={form.api_base} placeholder="https://…" /></label>{/if}
      {#if provider.fields.includes('api_version')}<label>API version <input bind:value={form.api_version} placeholder="2024-02-15-preview" /></label>{/if}
      {#if provider.fields.includes('aws_region_name')}<label>AWS region <input bind:value={form.aws_region_name} placeholder="us-east-1" /></label>{/if}
      <div class="row">
        <button class="primary" onclick={addModel} disabled={store.applying || !form.modelName || !form.modelId}>Save &amp; apply</button>
        <button onclick={resetForm}>Cancel</button>
      </div>
      <p class="hint">Secrets are stored as <code>os.environ/VAR</code> — set the real value in <code>.env</code>. Config holds no secrets.</p>
    </div>
  {/if}

  <div class="card">
    {#if models().length === 0}<p class="empty">No models yet. Add one to start serving.</p>
    {:else}
      <table>
        <thead><tr><th>Model name</th><th>litellm model</th><th></th></tr></thead>
        <tbody>
          {#each models() as m, i}
            <tr><td>{m.model_name}</td><td><code>{m.litellm_params?.model}</code></td>
              <td><button class="danger" onclick={() => deleteModel(i)} disabled={store.applying}>Delete</button></td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:960px}
  header{display:flex;align-items:center;justify-content:space-between}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.add{display:flex;flex-direction:column;gap:10px;max-width:520px}
  label{display:flex;flex-direction:column;font-size:13px;color:#3a3a3c;gap:4px}
  input,select{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .row{display:flex;gap:8px;margin-top:4px}
  button{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}
  button.danger{color:#ff3b30;border-color:#ffd0cc}
  button:disabled{opacity:.5;cursor:default}
  .banner{padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:#6e6e73}.empty{color:#6e6e73}
</style>
```

- [ ] **Step 2: build** `cd ui/frontend && npm run build` → success. Commit:
```bash
git add ui/frontend/src/routes/Models.svelte
git commit -m "feat(ui): Models screen (provider-driven CRUD on safe-apply)"
```

---

## Task 3: Routing screen

**Files:** Create `ui/frontend/src/routes/Routing.svelte`.

- [ ] **Step 1: implement `Routing.svelte`** — edit `router_settings.routing_strategy` (valid enum dropdown) + `num_retries` + simple fallbacks JSON; Save & apply:
```svelte
<script>
  import { onMount } from 'svelte'
  let { store } = $props()
  const STRATEGIES = ['simple-shuffle','least-busy','usage-based-routing','usage-based-routing-v2','latency-based-routing','cost-based-routing']
  let strategy = $state('simple-shuffle')
  let numRetries = $state('')
  let fallbacksText = $state('[]')
  let parseErr = $state('')
  onMount(async () => { if (!store.config) await store.load(); sync() })
  function sync() {
    const rs = store.config?.router_settings ?? {}
    strategy = rs.routing_strategy ?? 'simple-shuffle'
    numRetries = rs.num_retries ?? ''
    fallbacksText = JSON.stringify(rs.fallbacks ?? [], null, 2)
  }
  async function save() {
    parseErr = ''
    let fallbacks
    try { fallbacks = JSON.parse(fallbacksText) } catch (e) { parseErr = 'Fallbacks must be valid JSON'; return }
    const rs = { ...(store.config?.router_settings ?? {}), routing_strategy: strategy, fallbacks }
    if (numRetries !== '' && numRetries != null) rs.num_retries = Number(numRetries); else delete rs.num_retries
    await store.saveSection('router_settings', rs)
  }
</script>

<div class="page">
  <h1>Routing</h1>
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}
  <div class="card">
    <label>Routing strategy
      <select bind:value={strategy}>{#each STRATEGIES as s}<option value={s}>{s}</option>{/each}</select>
    </label>
    <p class="hint">Cost-based picks the cheapest deployment in a model group. <code>lowest-cost</code> is not valid and is rejected.</p>
    <label>Num retries <input type="number" min="0" bind:value={numRetries} placeholder="default 3" /></label>
    <label>Fallbacks (JSON, e.g. <code>[{'{'}"gpt-4": ["gpt-4o"]{'}'}]</code>)
      <textarea rows="5" bind:value={fallbacksText}></textarea>
    </label>
    {#if parseErr}<div class="banner err">{parseErr}</div>{/if}
    <div class="row">
      <button class="primary" onclick={save} disabled={store.applying}>Save &amp; apply</button>
      <button onclick={sync} disabled={store.applying}>Reset</button>
    </div>
  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:720px}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff;display:flex;flex-direction:column;gap:12px}
  label{display:flex;flex-direction:column;font-size:13px;color:#3a3a3c;gap:4px}
  select,input,textarea{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit}
  textarea{font-family:ui-monospace,monospace}
  .row{display:flex;gap:8px}
  button{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}button:disabled{opacity:.5}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:#6e6e73}
</style>
```

- [ ] **Step 2: build** + commit:
```bash
cd ui/frontend && npm run build
git add ui/frontend/src/routes/Routing.svelte
git commit -m "feat(ui): Routing screen (strategy + retries + fallbacks on safe-apply)"
```

---

## Task 4: Wire nav (App.svelte) + shared store

**Files:** Modify `ui/frontend/src/App.svelte`.

- [ ] **Step 1: add Models + Routing to the sidebar + a shared config store.** In `App.svelte`'s `<script>`, create the store once and pass it to the screens; add nav buttons + render branches:
```svelte
  import Models from './routes/Models.svelte'
  import Routing from './routes/Routing.svelte'
  import { createConfigStore } from './lib/configStore.svelte.js'
  const store = createConfigStore()
```
Add under the "Configuration" nav group (before the config.yaml viewer):
```svelte
      <button class="nav" class:active={screen==='models'} onclick={() => screen='models'}>◳ Models</button>
      <button class="nav" class:active={screen==='routing'} onclick={() => screen='routing'}>⇄ Routing</button>
```
And in the `<main>` render switch:
```svelte
      {#if screen==='dash'}<Dashboard />
      {:else if screen==='models'}<Models {store} />
      {:else if screen==='routing'}<Routing {store} />
      {:else}<ConfigViewer />{/if}
```

- [ ] **Step 2: build** `cd ui/frontend && npm run build` → success. Commit:
```bash
git add ui/frontend/src/App.svelte
git commit -m "feat(ui): wire Models + Routing into the sidebar nav"
```

---

## Task 5: Real-stack integration verification

**Files:** none.

- [ ] **Step 1:** `docker compose build llm-proxy-ui && docker compose up -d --wait`. Log in at `http://10.0.20.85:8081` (password from `.env`).
- [ ] **Step 2:** On **Models**: add an OpenAI model (name `gpt-4o-mini`, id `gpt-4o-mini`, key env `OPENAI_API_KEY`) → **Save & apply** → after the ~25s "applying" state, the model appears in the list and in `config/config.yaml`; confirm `curl -s -H "Authorization: Bearer $MK" http://localhost:4000/v1/models` lists it.
- [ ] **Step 3:** On **Routing**: switch strategy to `cost-based-routing` → Save & apply → succeeds; switch to an invalid value isn't possible (dropdown only). Confirm `config.yaml` shows the new strategy + the master_key/database_url/cache sections are STILL present (full round-trip preserved them).
- [ ] **Step 4:** Tear down (`docker compose down`), restore config (`git checkout config/config.yaml`).
- [ ] **Step 5:** Commit a docs note (admin-ui.md: Models/Routing screens are live).

## Self-Review
- **Spec coverage:** Models CRUD ✓ (T2), Routing editor ✓ (T3), safe-apply round-trip (full config, never partial — the P2.7 finding) ✓ (T1 saveSession), 422/409 UX ✓ (T1), nav ✓ (T4), real-stack proof ✓ (T5).
- **Round-trip safety:** `saveSection` spreads the full `config` and replaces one section → `master_key`/`database_url`/`cache_params` are never dropped.
- **No secrets in browser→config:** api_key is emitted as `os.environ/<VAR>`; the UI never puts a literal secret into config.yaml.

## Follow-on
Phase 3 (virtual keys + budgets via the litellm management API), Phase 4 (spend), Phase 5 (caching + housekeeping + export/import + dark mode), then docs + screenshots + LiteLLM credit.
