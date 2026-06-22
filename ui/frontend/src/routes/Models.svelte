<script>
  import { onMount } from 'svelte'
  import { FALLBACK_PROVIDERS, PINNED_PROVIDERS, ALL_MODES, SPECIAL_PROVIDER_FIELDS, CUSTOM_PROVIDERS, buildLitellmParams, modeLabel, perTokenToPerM, perMToPerToken } from '../lib/providers.js'
  import { api } from '../lib/api.js'
  import { uuidv4 } from '../lib/browser.js'
  let { store } = $props()
  let showAdd = $state(false)
  let editingId = $state(null)
  let providers = $state(FALLBACK_PROVIDERS)     // catalog list (or fallback)
  let providerSlug = $state('openai')
  let showAdvanced = $state(false)               // reveals api_base for custom/self-hosted
  let form = $state({ modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '', vertex_project: '', vertex_location: '', credential: '', mode: 'chat', input_cost: '', output_cost: '', timeout: '', disableHealthCheck: false })
  let healthMap = $state({})   // model_name → true(healthy) | false(unhealthy) | undefined(unknown)
  let busy = $state(false)
  let testResult = $state(null)  // { ok: bool, msg: string } | null
  let autofilled = $state(false)
  let autofillBusy = $state(false)
  let pendingNoKey = $state(false)
  let baseErr = $state(false)

  // Clear the keyless warning as soon as the user picks a credential or env var
  $effect(() => {
    if (form.credential || form.api_key_env) pendingNoKey = false
  })
  // Clear the missing-base error once a base URL is supplied (or the provider isn't custom)
  $effect(() => {
    if (form.api_base?.trim() || !CUSTOM_PROVIDERS.has(providerSlug)) baseErr = false
  })

  // Derived: model items and credential items from the item store
  let modelItems = $derived(store.itemsOfKind('model'))
  let credentialItems = $derived(store.itemsOfKind('credential'))

  onMount(async () => {
    // Load health map (non-fatal)
    try {
      const h = await api.modelsHealth()
      const map = {}
      for (const ep of (h.healthy_endpoints ?? [])) {
        if (ep.model_id) map[ep.model_id] = true
      }
      for (const ep of (h.unhealthy_endpoints ?? [])) {
        if (ep.model_id) map[ep.model_id] = false
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
    await loadDrift()
  })

  function resetForm() {
    form = { modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '', vertex_project: '', vertex_location: '', credential: '', mode: 'chat', input_cost: '', output_cost: '', timeout: '', disableHealthCheck: false }
    providerSlug = 'openai'
    showAdvanced = false
    showAdd = false
    testResult = null
    autofilled = false
    editingId = null
    pendingNoKey = false
    baseErr = false
  }

  function editModel(item) {
    const d = item.data, lp = d.litellm_params || {}
    const full = lp.model || ''
    const slash = full.indexOf('/')
    providerSlug = slash > 0 ? full.slice(0, slash) : 'openai'
    form = { modelName: d.model_name, modelId: slash > 0 ? full.slice(slash+1) : full,
      api_key_env: '', api_base: lp.api_base || '', api_version: lp.api_version || '',
      aws_region_name: lp.aws_region_name || '', vertex_project: lp.vertex_project || '', vertex_location: lp.vertex_location || '',
      credential: lp.litellm_credential_name || '', mode: (d.model_info||{}).mode || 'chat',
      input_cost: lp.input_cost_per_token!=null ? perTokenToPerM(lp.input_cost_per_token) : '',
      output_cost: lp.output_cost_per_token!=null ? perTokenToPerM(lp.output_cost_per_token) : '',
      timeout: lp.timeout != null ? lp.timeout : '',
      disableHealthCheck: !!((d.model_info||{}).disable_background_health_check) }
    editingId = item.name; showAdd = true; testResult = null; autofilled = false
    showAdvanced = !!(lp.api_base) || CUSTOM_PROVIDERS.has(providerSlug)
  }

  function currentProvider() { return providers.find(p => p.provider === providerSlug) || { provider: providerSlug } }
  function providerModes() {
    const m = currentProvider().modes
    return (Array.isArray(m) && m.length) ? m : ALL_MODES
  }
  function specialFields() { return SPECIAL_PROVIDER_FIELDS[providerSlug] || [] }
  function onProviderChange() {
    testResult = null; autofilled = false
    // clear special/deployment fields so a previous provider's values can't leak into params
    form.api_base = ''; form.api_version = ''; form.aws_region_name = ''
    form.vertex_project = ''; form.vertex_location = ''
    showAdvanced = CUSTOM_PROVIDERS.has(providerSlug)   // auto-open Advanced (api_base) for custom/local providers
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
        if (!form.input_cost) form.input_cost = perTokenToPerM(m.input_cost_per_token)
        if (!form.output_cost) form.output_cost = perTokenToPerM(m.output_cost_per_token)
        if (m.mode) form.mode = m.mode
        autofilled = true
      }
    } catch (_) { /* 404 = not in catalog; leave fields */ }
    finally { autofillBusy = false }
  }

  function buildParams() {
    const lp = buildLitellmParams(providerSlug, form)
    if (form.credential) { delete lp.api_key; lp.litellm_credential_name = form.credential }
    if (form.input_cost !== '' && form.input_cost != null) lp.input_cost_per_token = perMToPerToken(form.input_cost)
    if (form.output_cost !== '' && form.output_cost != null) lp.output_cost_per_token = perMToPerToken(form.output_cost)
    if (form.timeout !== '' && form.timeout != null) lp.timeout = Number(form.timeout)
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

  async function saveModel() {
    // Sanity gate: custom/local endpoints (custom_openai/ollama/vllm/…) REQUIRE a base URL.
    // Without it LiteLLM falls through to api.openai.com → 401. Hard-block (no override).
    if (CUSTOM_PROVIDERS.has(providerSlug) && !form.api_base?.trim()) {
      baseErr = true; showAdvanced = true; return
    }
    if (!form.credential && !form.api_key_env && !pendingNoKey) { pendingNoKey = true; return }
    pendingNoKey = false
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
  }

  // LiteLLM only honors per-model disable_background_health_check when this global
  // flag is set. Stage it once (idempotent) the first time any model disables its check.
  async function ensureHealthSkipFlag() {
    const exists = store.itemsOfKind('general_setting')
      .some(i => i.name === 'health_check_skip_disabled_background_models')
    if (!exists) await store.stageItem('general_setting', 'health_check_skip_disabled_background_models', true)
  }

  async function deleteModel(name) {
    await store.deleteItem('model', name)
  }

  async function undoDelete(name) {
    await store.discard('model', name)
  }

  function healthInfo(item) {
    const st = healthMap[item.name]
    if (st === true) return { color:'#34c759', title:'Healthy' }
    if (st === false) return { color:'#ff3b30', title:'Unhealthy' }
    if (item.flag === 'new') return { color:'#c7c7cc', title:'Not applied yet — apply to start health checks' }
    return { color:'#8e8e93', title:'Health check pending (background check runs every ~5 min)' }
  }

  let drift = $state(null)   // { hybrid, in_sync, missing_in_litellm:[], extra_in_litellm:[], content_drifted:[] } | null
  let resyncMsg = $state(null)   // { ok: boolean, text: string } | null
  async function loadDrift() {
    try { drift = await api.drift() } catch (_) { drift = null }
  }
  async function resyncToProxy() {
    resyncMsg = null
    let d
    try { d = await api.drift() } catch (e) { resyncMsg = { ok: false, text: e.message }; return }
    if (!d.hybrid) return
    if (d.in_sync) { resyncMsg = { ok: true, text: 'Already in sync with the proxy.' }; return }
    const miss = d.missing_in_litellm || [], extra = d.extra_in_litellm || [], upd = d.content_drifted || []
    const plan = `Resync to proxy:\n  + add ${miss.length}: ${miss.map(m => m.model_name).join(', ') || '—'}\n  ~ update ${upd.length}: ${upd.map(m => m.model_name).join(', ') || '—'}\n  - delete ${extra.length}: ${extra.map(m => m.model_name).join(', ') || '—'}\n\nProceed?`
    if (!confirm(plan)) return
    try {
      const r = await api.resync()
      resyncMsg = { ok: true, text: `Resynced — ${r.added} added, ${r.updated} updated, ${r.deleted} deleted${(r.failed && r.failed.length) ? `, ${r.failed.length} failed` : ''}.` }
    } catch (e) { resyncMsg = { ok: false, text: e.message } }
    await loadDrift()
  }

  // Reload drift after a successful Apply
  let _prevApplying = false
  $effect(() => {
    const cur = store.applying
    if (_prevApplying && !cur && !store.error) loadDrift()
    _prevApplying = cur
  })

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

  // Flag helpers
  function flagAccent(flag) {
    if (flag === 'new') return 'row-new'
    if (flag === 'changed') return 'row-changed'
    if (flag === 'deleted') return 'row-deleted'
    return ''
  }
</script>

<div class="page">
  <header><h1>Models</h1>
    {#if drift && drift.hybrid}
      <span class="drift" class:ok={drift.in_sync} class:warn={!drift.in_sync}
        title={drift.in_sync ? 'ui_config and the proxy agree' : 'ui_config and the proxy differ'}>
        {drift.in_sync ? 'In sync ✓' : `⚠ ${(drift.missing_in_litellm.length + drift.extra_in_litellm.length + (drift.content_drifted?.length || 0))} out of sync`}
      </span>
      {#if !drift.in_sync}
        <button onclick={resyncToProxy} disabled={store.applying || store.saving}>Resync to proxy</button>
      {/if}
    {/if}
    {#if resyncMsg}<div class="banner {resyncMsg.ok ? 'ok' : 'err'}">{resyncMsg.text}</div>{/if}
    <button class="primary" onclick={() => { editingId = null; showAdd = !showAdd }} disabled={store.applying}>＋ Add model</button>
  </header>
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
  {#if store.applying}<div class="banner info">{store.storeModelInDb ? 'Applying changes…' : 'Applying… restarting the proxy (~25s)'}</div>{/if}

  {#if showAdd}
    <div class="card add">
      <h3 style="margin:0 0 4px">{editingId ? 'Edit model' : 'Add model'}</h3>
      <label>Provider
        <select bind:value={providerSlug} onchange={onProviderChange}>
          {#each providers as p}<option value={p.provider}>{p.display_name || p.provider}</option>{/each}
        </select>
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
          {#each credentialItems as c}<option value={c.name}>{c.name}</option>{/each}
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
        <select bind:value={form.mode}>
          {#each providerModes() as m}<option value={m}>{modeLabel(m)}</option>{/each}
        </select>
        <span class="hint">The endpoint type used for the health check.</span>
      </label>

      <label class="check"><input type="checkbox" bind:checked={form.disableHealthCheck} />
        Disable background health check
        <span class="hint">Recommended for paid providers (e.g. deepinfra) — the background check sends a real billed request on each interval. Use "Check now" on demand instead.</span>
      </label>

      <!-- Advanced: custom endpoint (LiteLLM resolves the URL from the prefix otherwise) -->
      <button type="button" class="link" onclick={() => showAdvanced = !showAdvanced}>{showAdvanced ? '▾' : '▸'} Advanced: custom endpoint</button>
      {#if showAdvanced || specialFields().includes('api_base')}
        <label>API base (override / self-hosted) <input bind:value={form.api_base} placeholder="https://your-endpoint/v1 — leave blank to let LiteLLM resolve" /></label>
      {/if}
      {#if showAdvanced}
        <label>Timeout (s) <input type="number" min="0" step="1" bind:value={form.timeout} placeholder="blank = use router/global timeout" />
          <span class="hint">Per-deployment request timeout (total, incl. generation). Leave blank to inherit the router/global timeout. Set a short value on fast cloud backends so a hung call fails over quickly; leave blank/high on slow local backends.</span>
        </label>
      {/if}

      <label>Input cost ($ / 1M tokens) <input type="number" step="0.001" min="0" bind:value={form.input_cost} placeholder="auto from catalog" /></label>
      <label>Output cost ($ / 1M tokens) <input type="number" step="0.001" min="0" bind:value={form.output_cost} placeholder="auto from catalog" /></label>

      {#if baseErr}
        <div class="banner err">This is a custom / self-hosted provider, so an <b>API base</b> URL is required.
          Without it LiteLLM falls through to <code>api.openai.com</code> and the request fails with a 401.
          Set the API base above, then Save.</div>
      {/if}
      {#if pendingNoKey}
        <div class="banner warn">This deployment has no API key. LiteLLM requires one even for local providers
          (vLLM/llama.cpp) — requests will fail without it. Pick a saved credential (a reusable dummy key works)
          or set an API-key env var. Click Save again to save anyway.</div>
      {/if}

      <div class="row">
        <button onclick={testConn} disabled={busy || !form.modelName || !form.modelId}>Test connection</button>
        <button class="primary" onclick={saveModel} disabled={store.applying || store.saving || !form.modelName || !form.modelId}>Save</button>
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
    {#if modelItems.length === 0}<p class="empty">No models yet. Add one to start serving.</p>
    {:else}
      <table>
        <thead><tr><th>Model name</th><th>litellm model</th><th>Costs</th><th>Health</th><th>Check</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {#each modelItems as item}
            {@const publicName = item.data?.model_name ?? item.name}
            {@const lp = item.data?.litellm_params ?? {}}
            {@const dot = healthInfo(item)}
            {@const flag = item.flag}
            {@const inCost = lp.input_cost_per_token != null ? perTokenToPerM(lp.input_cost_per_token).toFixed(2) : null}
            {@const outCost = lp.output_cost_per_token != null ? perTokenToPerM(lp.output_cost_per_token).toFixed(2) : null}
            <tr class={flagAccent(flag)}>
              <td class:strikethrough={flag === 'deleted'}>{publicName}</td>
              <td class:strikethrough={flag === 'deleted'}><code>{lp.model ?? ''}</code></td>
              <td class:strikethrough={flag === 'deleted'} style="font-size:12px;color:#6e6e73">
                {#if inCost != null || outCost != null}
                  In: ${inCost ?? '—'} Out: ${outCost ?? '—'} / 1M
                {:else}—{/if}
              </td>
              <td><span class="dot" style="background:{dot.color}" title={dot.title}></span></td>
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
              <td>
                {#if flag === 'new'}<span class="flag-tag flag-new">new</span>
                {:else if flag === 'changed'}<span class="flag-tag flag-changed">changed</span>
                {:else if flag === 'deleted'}<span class="flag-tag flag-deleted">deleted</span>
                {/if}
              </td>
              <td>
                {#if flag === 'deleted'}
                  <button class="undo" onclick={() => undoDelete(item.name)} disabled={store.saving || store.applying}>Undo</button>
                {:else}
                  <button onclick={() => editModel(item)} disabled={store.saving || store.applying}>Edit</button>
                  <button class="danger" onclick={() => deleteModel(item.name)} disabled={store.saving || store.applying}>Delete</button>
                {/if}
              </td>
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
  button.undo{color:#ff9500;border-color:#ffe0b2}
  button:disabled{opacity:.5;cursor:default}
  .banner{padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}.banner.warn{background:#fff8e1;color:#7a4800}
  .hint{font-size:12px;color:#6e6e73}.empty{color:#6e6e73}
  label.check{flex-direction:row;align-items:flex-start;gap:8px;flex-wrap:wrap}
  label.check input{margin-top:2px}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%}
  .check-res{margin-left:6px;font-weight:600}
  .check-res.ok{color:#34c759}
  .check-res.bad{color:#ff3b30}
  .lookup-row{display:flex;gap:6px;align-items:stretch}
  .lookup-row input{flex:1}
  .lookup-row button{white-space:nowrap;padding:8px 10px;font-size:12px}
  .autofill-hint{font-size:11px;color:#1d7a33;margin-top:2px}
  .prefix{display:inline-flex;align-items:center;padding:0 8px;background:#f0f0f3;border:1px solid #ccc;border-right:0;border-radius:8px 0 0 8px;font:inherit;color:#6e6e73;white-space:nowrap}
  .lookup-row .prefix + input{border-radius:0}
  button.link{background:none;border:0;color:#0a84ff;cursor:pointer;font-size:12px;padding:0;text-align:left;width:fit-content}

  /* Drift badge */
  .drift{font-size:12px;padding:3px 10px;border-radius:20px}
  .drift.ok{background:#e7f7ec;color:#1d7a33}
  .drift.warn{background:#fff4e5;color:#9a5b00}

  /* Flag row accents */
  .row-new{background:rgba(10,132,255,.06)}
  .row-changed{background:rgba(255,149,0,.06)}
  .row-deleted{background:rgba(255,59,48,.05)}
  .strikethrough{text-decoration:line-through;color:#8e8e93}

  /* Flag tags */
  .flag-tag{display:inline-block;font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;text-transform:uppercase;letter-spacing:.04em}
  .flag-new{background:rgba(10,132,255,.12);color:#0a52c7}
  .flag-changed{background:rgba(255,149,0,.15);color:#b36800}
  .flag-deleted{background:rgba(255,59,48,.12);color:#c0271d}
</style>
