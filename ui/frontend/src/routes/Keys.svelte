<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { copyText } from '../lib/browser.js'
  let keys = $state([]); let err = $state(''); let loading = $state(false)
  let showCreate = $state(false); let busy = $state(false)
  let newKey = $state(null)   // the one-time plaintext key after create
  let availableModels = $state([])
  const STRATEGIES = ['simple-shuffle','least-busy','usage-based-routing','usage-based-routing-v2','latency-based-routing','cost-based-routing']
  const FALLBACKS_PLACEHOLDER = '[{"gpt-4": ["gpt-4o"]}]'
  let form = $state({ key_alias: '', models: [], max_budget: '', budget_duration: '', duration: '', rpm_limit: '', tpm_limit: '', router_strategy: '', router_fallbacks: '', router_num_retries: '', router_timeout: '', router_cooldown_time: '', router_allowed_fails: '', router_retry_after: '' })
  let showRouterSettings = $state(false)

  async function load() {
    loading = true; err = ''
    try {
      keys = await api.keys()
      const state = await api.configState().catch(() => ({ items: [] }))
      availableModels = (state.items || []).filter(i => i.kind === 'model').map(i => i.name)
    } catch (e) { err = e.message } finally { loading = false }
  }
  onMount(load)

  function num(v) { return v === '' || v == null ? undefined : Number(v) }
  async function create() {
    busy = true; err = ''; newKey = null
    const payload = { key_alias: form.key_alias || undefined, models: form.models,
      max_budget: num(form.max_budget), budget_duration: form.budget_duration || undefined,
      duration: form.duration || undefined, rpm_limit: num(form.rpm_limit), tpm_limit: num(form.tpm_limit) }
    // LiteLLM /key/generate top-level field — confirmed from its own UI request
    const rs = {}
    if (form.router_strategy) rs.routing_strategy = form.router_strategy
    if (form.router_fallbacks.trim()) {
      try { rs.fallbacks = JSON.parse(form.router_fallbacks) }
      catch { err = 'Router fallbacks must be valid JSON'; busy = false; return }
    }
    for (const [k, v] of [['num_retries', form.router_num_retries], ['timeout', form.router_timeout],
        ['cooldown_time', form.router_cooldown_time], ['allowed_fails', form.router_allowed_fails],
        ['retry_after', form.router_retry_after]]) {
      if (v !== '' && v != null) rs[k] = Number(v)
    }
    if (Object.keys(rs).length) payload.router_settings = rs
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
      <button onclick={() => copyText(newKey)}>Copy</button>
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
      <details bind:open={showRouterSettings}>
        <summary class="router-summary">Router Settings (optional)</summary>
        <div class="router-body">
          <label>Routing strategy
            <select bind:value={form.router_strategy}>
              <option value="">— use global default —</option>
              {#each STRATEGIES as s}<option value={s}>{s}</option>{/each}
            </select>
          </label>
          <label>Fallbacks
            <textarea bind:value={form.router_fallbacks} rows="3" placeholder={FALLBACKS_PLACEHOLDER}></textarea>
            <span class="hint">Optional. JSON list of {'{'}model: [fallback models]{'}'}.</span>
          </label>
          <div class="grid">
            <label>Num retries
              <input type="number" min="0" bind:value={form.router_num_retries} placeholder="e.g. 3" />
              <span class="hint">blank = inherit global</span>
            </label>
            <label>Timeout (s)
              <input type="number" min="0" step="0.1" bind:value={form.router_timeout} placeholder="e.g. 30" />
              <span class="hint">blank = inherit global</span>
            </label>
            <label>Cooldown time (s)
              <input type="number" min="0" bind:value={form.router_cooldown_time} placeholder="e.g. 60" />
              <span class="hint">blank = inherit global</span>
            </label>
            <label>Allowed fails
              <input type="number" min="0" bind:value={form.router_allowed_fails} placeholder="e.g. 3" />
              <span class="hint">blank = inherit global</span>
            </label>
            <label>Retry after (s)
              <input type="number" min="0" bind:value={form.router_retry_after} placeholder="e.g. 10" />
              <span class="hint">blank = inherit global</span>
            </label>
          </div>
        </div>
      </details>
      <div class="row"><button class="primary" onclick={create} disabled={busy}>Create</button><button onclick={() => { showCreate = false; form.router_strategy = ''; form.router_fallbacks = ''; form.router_num_retries = ''; form.router_timeout = ''; form.router_cooldown_time = ''; form.router_allowed_fails = ''; form.router_retry_after = ''; showRouterSettings = false }}>Cancel</button></div>
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
  details{border:1px solid rgba(0,0,0,.08);border-radius:8px;padding:0}
  .router-summary{padding:8px 10px;cursor:pointer;font-size:13px;color:#3a3a3c;user-select:none;list-style:none}
  .router-summary::marker,.router-summary::-webkit-details-marker{display:none}
  .router-summary::before{content:'▶';display:inline-block;margin-right:6px;font-size:10px;transition:transform .15s}
  details[open] .router-summary::before{transform:rotate(90deg)}
  .router-body{display:flex;flex-direction:column;gap:10px;padding:10px 10px 12px}
  textarea{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit;resize:vertical}
  .hint{font-size:11px;color:#6e6e73;margin-top:2px}
</style>
