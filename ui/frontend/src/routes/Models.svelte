<script>
  import { onMount } from 'svelte'
  import { FALLBACK_PROVIDERS, PINNED_PROVIDERS, ALL_MODES, SPECIAL_PROVIDER_FIELDS, buildLitellmParams } from '../lib/providers.js'
  import { api } from '../lib/api.js'
  let { store } = $props()
  let showAdd = $state(false)
  let providers = $state(FALLBACK_PROVIDERS)     // catalog list (or fallback)
  let providerSlug = $state('openai')
  let showAdvanced = $state(false)               // reveals api_base for custom/self-hosted
  let form = $state({ modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '', vertex_project: '', vertex_location: '', credential: '', mode: 'chat', input_cost: '', output_cost: '' })
  let credentials = $state([])
  let healthMap = $state({})   // model_name → true(healthy) | false(unhealthy) | undefined(unknown)
  let busy = $state(false)
  let testResult = $state(null)  // { ok: bool, msg: string } | null
  let autofilled = $state(false)
  let autofillBusy = $state(false)

  onMount(async () => {
    if (!store.config) store.load()
    // Load credentials for the dropdown (non-fatal)
    try { credentials = await api.credentials() } catch (_) { credentials = [] }
    // Load health map (non-fatal)
    try {
      const h = await api.modelsHealth()
      const map = {}
      for (const ep of (h.healthy_endpoints ?? [])) {
        const name = ep.model ?? ep.model_name
        if (name) map[name] = true
      }
      for (const ep of (h.unhealthy_endpoints ?? [])) {
        const name = ep.model ?? ep.model_name
        if (name) map[name] = false
      }
      healthMap = map
    } catch (_) { healthMap = {} }
    // Load catalog providers (fallback on error)
    try {
      const ps = await api.catalogProviders()
      if (Array.isArray(ps) && ps.length) {
        const pinned = PINNED_PROVIDERS.map(s => ps.find(p => p.provider === s)).filter(Boolean)
        const rest = ps.filter(p => !PINNED_PROVIDERS.includes(p.provider)).sort((a,b)=> (a.display_name||a.provider).localeCompare(b.display_name||b.provider))
        providers = [...pinned, ...rest]
      }
    } catch (_) { providers = FALLBACK_PROVIDERS }
  })

  function models() { return store.config?.model_list ?? [] }

  function resetForm() {
    form = { modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '', vertex_project: '', vertex_location: '', credential: '', mode: 'chat', input_cost: '', output_cost: '' }
    providerSlug = 'openai'
    showAdvanced = false
    showAdd = false
    testResult = null
    autofilled = false
  }

  function currentProvider() { return providers.find(p => p.provider === providerSlug) || { provider: providerSlug } }
  function providerModes() {
    const m = currentProvider().modes
    return (Array.isArray(m) && m.length) ? m : ALL_MODES
  }
  function specialFields() { return SPECIAL_PROVIDER_FIELDS[providerSlug] || [] }
  function onProviderChange() {
    testResult = null; autofilled = false
    const modes = providerModes()
    if (!modes.includes(form.mode)) form.mode = modes[0] || 'chat'
  }

  async function tryAutofill() {
    if (!form.modelId) return
    const full = providerSlug + '/' + form.modelId
    autofillBusy = true
    try {
      const m = await api.catalogModel(full)
      if (m) {
        if (!form.input_cost) form.input_cost = m.input_cost_per_token ?? ''
        if (!form.output_cost) form.output_cost = m.output_cost_per_token ?? ''
        if (m.mode) form.mode = m.mode
        autofilled = true
      }
    } catch (_) { /* 404 = not in catalog; leave fields */ }
    finally { autofillBusy = false }
  }

  function buildParams() {
    const lp = buildLitellmParams(providerSlug, form)
    if (form.credential) { delete lp.api_key; lp.litellm_credential_name = form.credential }
    if (form.input_cost !== '' && form.input_cost !== null) lp.input_cost_per_token = Number(form.input_cost)
    if (form.output_cost !== '' && form.output_cost !== null) lp.output_cost_per_token = Number(form.output_cost)
    return lp
  }

  async function testConn() {
    busy = true
    testResult = null
    try {
      const r = await api.testModel({ litellm_params: buildParams(), mode: form.mode })
      testResult = { ok: r.status === 'success', msg: r.status === 'success' ? 'Connection successful' : `Error: ${typeof r.result === 'string' ? r.result : JSON.stringify(r.result)}` }
    } catch (e) {
      testResult = { ok: false, msg: `Error: ${e.message}` }
    } finally {
      busy = false
    }
  }

  async function addModel() {
    const entry = {
      model_name: form.modelName,
      litellm_params: buildParams(),
      model_info: { mode: form.mode }
    }
    const ok = await store.saveSection('model_list', [...models(), entry])
    if (ok) resetForm()   // keep the user's input on a rejected save (422)
  }

  async function deleteModel(i) {
    await store.saveSection('model_list', models().filter((_, j) => j !== i))
  }

  function healthDot(modelName) {
    const status = healthMap[modelName]
    if (status === true) return { color: '#34c759', title: 'Healthy' }
    if (status === false) return { color: '#ff3b30', title: 'Unhealthy' }
    return { color: '#8e8e93', title: 'Unknown' }
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
        <input list="provider-list" bind:value={providerSlug} onchange={onProviderChange} placeholder="search providers…" />
        <datalist id="provider-list">
          {#each providers as p}<option value={p.provider}>{p.display_name || p.provider}</option>{/each}
        </datalist>
      </label>
      <label>Public model name <input bind:value={form.modelName} placeholder="e.g. gpt-4o" /></label>
      <label>Provider model id
        <div class="lookup-row">
          <span class="prefix">{providerSlug}/</span>
          <input bind:value={form.modelId} placeholder="e.g. gpt-4o" onblur={tryAutofill} />
          <button type="button" onclick={tryAutofill} disabled={autofillBusy || !form.modelId}>{autofillBusy ? '…' : 'Look up pricing'}</button>
        </div>
        {#if autofilled}<span class="autofill-hint">auto-filled from catalog</span>{/if}
      </label>

      <label>Credential
        <select bind:value={form.credential}>
          <option value="">— env var / none —</option>
          {#each credentials as c}<option value={c.credential_name}>{c.credential_name}</option>{/each}
        </select>
      </label>
      {#if !form.credential}
        <label>API key env var <input bind:value={form.api_key_env} placeholder="e.g. OPENAI_API_KEY" /></label>
      {/if}

      <!-- Special per-provider deployment fields (curated) -->
      {#if specialFields().includes('api_version')}<label>API version <input bind:value={form.api_version} placeholder="2024-02-15-preview" /></label>{/if}
      {#if specialFields().includes('aws_region_name')}<label>AWS region <input bind:value={form.aws_region_name} placeholder="us-east-1" /></label>{/if}
      {#if specialFields().includes('vertex_project')}<label>Vertex project <input bind:value={form.vertex_project} placeholder="my-gcp-project" /></label>{/if}
      {#if specialFields().includes('vertex_location')}<label>Vertex location <input bind:value={form.vertex_location} placeholder="us-central1" /></label>{/if}

      <label>Mode
        <select bind:value={form.mode}>{#each providerModes() as m}<option value={m}>{m}</option>{/each}</select>
      </label>

      <!-- Advanced: custom endpoint (LiteLLM resolves the URL from the prefix otherwise) -->
      <button type="button" class="link" onclick={() => showAdvanced = !showAdvanced}>{showAdvanced ? '▾' : '▸'} Advanced: custom endpoint</button>
      {#if showAdvanced || specialFields().includes('api_base')}
        <label>API base (override / self-hosted) <input bind:value={form.api_base} placeholder="https://your-endpoint/v1 — leave blank to let LiteLLM resolve" /></label>
      {/if}

      <label>Input cost/token <input type="number" step="1e-9" min="0" bind:value={form.input_cost} placeholder="auto from catalog" /></label>
      <label>Output cost/token <input type="number" step="1e-9" min="0" bind:value={form.output_cost} placeholder="auto from catalog" /></label>

      <div class="row">
        <button onclick={testConn} disabled={busy || !form.modelName || !form.modelId}>Test connection</button>
        <button class="primary" onclick={addModel} disabled={store.applying || !form.modelName || !form.modelId}>Save</button>
        <button onclick={resetForm}>Cancel</button>
      </div>

      {#if testResult}<div class="banner {testResult.ok ? 'ok' : 'err'}">{testResult.msg}</div>{/if}

      {#if !form.credential}
        <p class="hint">Secrets are stored as <code>os.environ/VAR</code> — set the real value in <code>.env</code>. LiteLLM resolves the endpoint URL from the provider prefix; set API base only for self-hosted or custom deployments. Config holds no secrets.</p>
      {:else}
        <p class="hint">Using saved credential <strong>{form.credential}</strong> — no env var needed. Apply to activate.</p>
      {/if}
    </div>
  {/if}

  <div class="card">
    {#if models().length === 0}<p class="empty">No models yet. Add one to start serving.</p>
    {:else}
      <table>
        <thead><tr><th>Model name</th><th>litellm model</th><th>Health</th><th></th></tr></thead>
        <tbody>
          {#each models() as m, i}
            {@const dot = healthDot(m.model_name)}
            <tr>
              <td>{m.model_name}</td>
              <td><code>{m.litellm_params?.model}</code></td>
              <td><span class="dot" style="background:{dot.color}" title={dot.title}></span></td>
              <td><button class="danger" onclick={() => deleteModel(i)} disabled={store.applying}>Delete</button></td>
            </tr>
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
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%}
  .lookup-row{display:flex;gap:6px;align-items:stretch}
  .lookup-row input{flex:1}
  .lookup-row button{white-space:nowrap;padding:8px 10px;font-size:12px}
  .autofill-hint{font-size:11px;color:#1d7a33;margin-top:2px}
  .prefix{display:inline-flex;align-items:center;padding:0 8px;background:#f0f0f3;border:1px solid #ccc;border-right:0;border-radius:8px 0 0 8px;font:inherit;color:#6e6e73;white-space:nowrap}
  .lookup-row .prefix + input{border-radius:0}
  button.link{background:none;border:0;color:#0a84ff;cursor:pointer;font-size:12px;padding:0;text-align:left;width:fit-content}
</style>
