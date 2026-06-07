<script>
  import { onMount } from 'svelte'
  import { PROVIDERS, buildLitellmParams } from '../lib/providers.js'
  import { api } from '../lib/api.js'
  let { store } = $props()
  let showAdd = $state(false)
  let provider = $state(PROVIDERS[0])
  let form = $state({ modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '', credential: '', mode: 'chat', input_cost: '', output_cost: '' })
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
  })

  function models() { return store.config?.model_list ?? [] }

  function resetForm() {
    form = { modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '', credential: '', mode: 'chat', input_cost: '', output_cost: '' }
    provider = PROVIDERS[0]
    showAdd = false
    testResult = null
    autofilled = false
  }

  async function tryAutofill() {
    if (!form.modelId) return
    const full = (provider.prefix || '') + form.modelId
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
    const lp = buildLitellmParams(provider, form)
    // Credential path: if a named credential is selected, add litellm_credential_name and
    // remove any api_key the env-var path may have emitted (the two paths are mutually exclusive).
    if (form.credential) {
      delete lp.api_key
      lp.litellm_credential_name = form.credential
    }
    // Custom costs (optional)
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
        <select bind:value={provider} onchange={() => { testResult = null; autofilled = false }}>{#each PROVIDERS as p}<option value={p}>{p.label}</option>{/each}</select>
      </label>
      <label>Public model name <input bind:value={form.modelName} placeholder="e.g. gpt-4o" /></label>
      <label>Provider model id
        <div class="lookup-row">
          <input bind:value={form.modelId} placeholder="e.g. gpt-4o (→ {provider.prefix}…)" onblur={tryAutofill} />
          <button type="button" onclick={tryAutofill} disabled={autofillBusy || !form.modelId} title="Look up pricing from LiteLLM catalog">{autofillBusy ? '…' : 'Look up pricing'}</button>
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
        {#if provider.fields.includes('api_key')}
          <label>API key env var <input bind:value={form.api_key_env} placeholder={provider.keyEnv || 'MY_API_KEY'} /></label>
        {/if}
      {/if}

      {#if provider.fields.includes('api_base')}<label>API base <input bind:value={form.api_base} placeholder="https://…" /></label>{/if}
      {#if provider.fields.includes('api_version')}<label>API version <input bind:value={form.api_version} placeholder="2024-02-15-preview" /></label>{/if}
      {#if provider.fields.includes('aws_region_name')}<label>AWS region <input bind:value={form.aws_region_name} placeholder="us-east-1" /></label>{/if}

      <label>Mode
        <select bind:value={form.mode}>
          {#each ['chat','embedding','completion','image_generation','audio_transcription','rerank','moderations'] as m}
            <option value={m}>{m}</option>
          {/each}
        </select>
      </label>

      <label>Input cost/token <input type="number" step="1e-9" min="0" bind:value={form.input_cost} placeholder="auto (v2.3)" /></label>
      <label>Output cost/token <input type="number" step="1e-9" min="0" bind:value={form.output_cost} placeholder="auto (v2.3)" /></label>

      <div class="row">
        <button onclick={testConn} disabled={busy || !form.modelName || !form.modelId}>Test connection</button>
        <button class="primary" onclick={addModel} disabled={store.applying || !form.modelName || !form.modelId}>Save</button>
        <button onclick={resetForm}>Cancel</button>
      </div>

      {#if testResult}<div class="banner {testResult.ok ? 'ok' : 'err'}">{testResult.msg}</div>{/if}

      {#if !form.credential}
        <p class="hint">Secrets are stored as <code>os.environ/VAR</code> — set the real value in <code>.env</code>. Config holds no secrets.</p>
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
</style>
