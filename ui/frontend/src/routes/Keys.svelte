<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { copyText } from '../lib/browser.js'
  import { rulesToFallbacks, fallbacksToRules } from '../lib/fallbacks.js'
  let keys = $state([]); let err = $state(''); let loading = $state(false)
  let showCreate = $state(false); let busy = $state(false)
  let newKey = $state(null)   // the one-time plaintext key after create
  let availableModels = $state([])
  let editingToken = $state(null)
  const STRATEGIES = ['simple-shuffle','least-busy','usage-based-routing','usage-based-routing-v2','latency-based-routing','cost-based-routing']
  const FALLBACKS_PLACEHOLDER = '[{"primary-model-name": ["backup-model-name"]}]'
  let form = $state({ key_alias: '', models: [], max_budget: '', budget_duration: '', duration: '', rpm_limit: '', tpm_limit: '', router_strategy: '', router_fallbacks: '', router_num_retries: '', router_timeout: '', router_cooldown_time: '', router_allowed_fails: '', router_retry_after: '' })
  let showRouterSettings = $state(false)
  // Fallbacks editor: structured picker by default, raw-JSON escape hatch for advanced cases ("*").
  let fbMode = $state('picker')   // 'picker' | 'json'
  let fbRules = $state([])        // [{ primary: string, backups: string[] }]
  let fbErr = $state('')
  // Picker options come from the key's Allowed models (or all models if unrestricted),
  // so a key can only ever fall back to models it is permitted to call.
  let fbOptions = $derived((form.models && form.models.length) ? form.models : availableModels)
  function resetFb() { fbMode = 'picker'; fbRules = []; fbErr = ''; form.router_fallbacks = '' }
  function addFbRule() { fbRules = [...fbRules, { primary: '', backups: [] }] }
  function rmFbRule(i) { fbRules = fbRules.filter((_, j) => j !== i) }
  function switchFbToJson() { form.router_fallbacks = JSON.stringify(rulesToFallbacks(fbRules), null, 2); fbErr = ''; fbMode = 'json' }
  function switchFbToPicker() {
    let val
    try { val = form.router_fallbacks.trim() ? JSON.parse(form.router_fallbacks) : [] }
    catch { fbErr = 'Invalid JSON — fix it or clear the box to use the picker.'; return }
    const { rules, representable } = fallbacksToRules(val)
    if (!representable) { fbErr = "This JSON can't be shown in the picker (e.g. it uses the \"*\" wildcard). Keep editing as JSON."; return }
    fbRules = rules; fbErr = ''; fbMode = 'picker'
  }

  async function load() {
    loading = true; err = ''
    try {
      keys = await api.keys()
      const state = await api.configState().catch(() => ({ items: [] }))
      availableModels = [...new Set(
        (state.items || [])
          .filter(i => i.kind === 'model')
          .map(i => i.data?.model_name)
          .filter(Boolean)
      )].sort()
    } catch (e) { err = e.message } finally { loading = false }
  }
  onMount(load)

  function num(v) { return v === '' || v == null ? undefined : Number(v) }

  function buildKeyFields() {
    const payload = {
      key_alias: form.key_alias || undefined,
      models: form.models,
      max_budget: num(form.max_budget),
      budget_duration: form.budget_duration || undefined,
      duration: form.duration || undefined,
      rpm_limit: num(form.rpm_limit),
      tpm_limit: num(form.tpm_limit)
    }
    const rs = {}
    if (form.router_strategy) rs.routing_strategy = form.router_strategy
    let fb
    if (fbMode === 'json') {
      if (form.router_fallbacks.trim()) {
        try { fb = JSON.parse(form.router_fallbacks) }
        catch { return null }  // caller checks for null on parse error
      }
    } else {
      fb = rulesToFallbacks(fbRules)
    }
    if (fb && fb.length) rs.fallbacks = fb
    for (const [k, v] of [['num_retries', form.router_num_retries], ['timeout', form.router_timeout],
        ['cooldown_time', form.router_cooldown_time], ['allowed_fails', form.router_allowed_fails],
        ['retry_after', form.router_retry_after]]) {
      if (v !== '' && v != null) rs[k] = Number(v)
    }
    if (Object.keys(rs).length) payload.router_settings = rs
    Object.keys(payload).forEach(k => payload[k] === undefined && delete payload[k])
    return payload
  }

  function editKey(k) {
    form = { ...form,
      key_alias: k.key_alias || '', models: (k.models || []),
      max_budget: k.max_budget ?? '', budget_duration: k.budget_duration || '',
      duration: '', rpm_limit: k.rpm_limit ?? '', tpm_limit: k.tpm_limit ?? '',
      router_strategy: (k.router_settings?.routing_strategy) || '',
      router_fallbacks: '',
      router_num_retries: k.router_settings?.num_retries ?? '', router_timeout: k.router_settings?.timeout ?? '',
      router_cooldown_time: k.router_settings?.cooldown_time ?? '', router_allowed_fails: k.router_settings?.allowed_fails ?? '',
      router_retry_after: k.router_settings?.retry_after ?? '' }
    // Load existing fallbacks into the picker; fall back to the JSON editor if they
    // can't be represented (e.g. a "*" wildcard) so nothing is silently dropped.
    const fb = k.router_settings?.fallbacks
    const { rules, representable } = fallbacksToRules(fb)
    if (fb && !representable) { fbMode = 'json'; fbRules = []; form.router_fallbacks = JSON.stringify(fb, null, 2) }
    else { fbMode = 'picker'; fbRules = rules }
    fbErr = ''
    editingToken = k.token; showCreate = true; showRouterSettings = !!k.router_settings
  }

  async function create() {
    busy = true; err = ''
    if (editingToken) {
      const fields = buildKeyFields()
      if (fields === null) { err = 'Router fallbacks must be valid JSON'; busy = false; return }
      try {
        await api.post('/api/keys/update', { key: editingToken, ...fields })
        editingToken = null; showCreate = false; await load()
      } catch (e) { err = e.message } finally { busy = false }
      return
    }
    newKey = null
    const payload = buildKeyFields()
    if (payload === null) { err = 'Router fallbacks must be valid JSON'; busy = false; return }
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
  <header><h1>Virtual Keys</h1><button class="primary" onclick={() => { editingToken = null; showCreate = true; newKey = null; resetFb() }} disabled={busy}>＋ New key</button></header>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if newKey && !editingToken}
    <div class="banner key">
      <strong>New key (copy it now — shown only once):</strong>
      <code>{newKey}</code>
      <button onclick={() => copyText(newKey)}>Copy</button>
      <button onclick={() => newKey = null}>Done</button>
    </div>
  {/if}

  {#if showCreate}
    <div class="card add">
      <h2 class="form-heading">{editingToken ? 'Edit key' : 'Create key'}</h2>
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
              <option value="">Inherit global</option>
              {#each STRATEGIES as s}<option value={s}>{s}</option>{/each}
            </select>
          </label>
          <label>Fallbacks
            {#if fbMode === 'picker'}
              {#if fbOptions.length === 0}
                <span class="hint">Add models on the Models screen first — fallbacks pick from this key's allowed models.</span>
              {/if}
              {#each fbRules as rule, i}
                <div class="fb-rule">
                  <select bind:value={rule.primary} aria-label="primary model">
                    <option value="">— primary —</option>
                    {#each fbOptions as m}<option value={m}>{m}</option>{/each}
                  </select>
                  <span class="fb-arrow">falls back to →</span>
                  <select multiple bind:value={rule.backups} aria-label="backup models"
                          size={Math.min(4, Math.max(2, fbOptions.length))}>
                    {#each fbOptions.filter(m => m !== rule.primary) as m}<option value={m}>{m}</option>{/each}
                  </select>
                  <button type="button" class="fb-rm" title="Remove" onclick={() => rmFbRule(i)}>✕</button>
                </div>
              {/each}
              <div class="fb-actions">
                <button type="button" class="fb-add" onclick={addFbRule}>+ Add fallback</button>
                <button type="button" class="linkbtn" onclick={switchFbToJson}>Advanced (JSON)</button>
              </div>
              <span class="hint">If the primary is failing or in cooldown, requests retry on the backup(s), in order. Choices come from this key's <strong>Allowed models</strong> above, so a key only falls back to models it can call.</span>
            {:else}
              <textarea bind:value={form.router_fallbacks} rows="4" placeholder={FALLBACKS_PLACEHOLDER}></textarea>
              <span class="hint">Advanced: raw LiteLLM fallbacks JSON (supports the <code>"*"</code> wildcard). Every model named must also be in <strong>Allowed models</strong>. <button type="button" class="linkbtn" onclick={switchFbToPicker}>Back to picker</button></span>
            {/if}
            {#if fbErr}<span class="fb-err">{fbErr}</span>{/if}
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
      <div class="row"><button class="primary" onclick={create} disabled={busy}>{editingToken ? 'Save' : 'Create'}</button><button onclick={() => { showCreate = false; editingToken = null; form.router_strategy = ''; form.router_num_retries = ''; form.router_timeout = ''; form.router_cooldown_time = ''; form.router_allowed_fails = ''; form.router_retry_after = ''; showRouterSettings = false; resetFb() }}>Cancel</button></div>
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
              <td class="actions">
                <button onclick={() => editKey(k)} disabled={busy}>Edit</button>
                <button class="danger" onclick={() => del(k.token)} disabled={busy}>Delete</button>
              </td>
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
  .fb-rule{display:flex;align-items:flex-start;gap:8px;margin-top:6px}
  .fb-rule select{flex:1;min-width:0}
  .fb-arrow{font-size:11px;color:#6e6e73;white-space:nowrap;align-self:center}
  .fb-rm{border:0;background:transparent;color:#b00020;cursor:pointer;font-size:14px;line-height:1;padding:4px}
  .fb-actions{display:flex;gap:12px;align-items:center;margin-top:8px}
  .fb-add{font-size:12px;padding:4px 10px;border:1px solid var(--border,rgba(0,0,0,.15));border-radius:7px;background:var(--card,#fff);cursor:pointer;color:inherit}
  .linkbtn{background:none;border:0;padding:0;color:#0a84ff;cursor:pointer;font:inherit;font-size:12px;text-decoration:underline}
  .fb-err{font-size:11px;color:#b00020;margin-top:4px}
  .form-heading{margin:0 0 4px;font-size:15px;font-weight:600;color:#1c1c1e}
  .actions{display:flex;gap:6px}
</style>
