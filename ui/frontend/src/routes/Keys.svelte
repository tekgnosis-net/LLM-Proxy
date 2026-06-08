<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let keys = $state([]); let err = $state(''); let loading = $state(false)
  let showCreate = $state(false); let busy = $state(false)
  let newKey = $state(null)   // the one-time plaintext key after create
  let availableModels = $state([])
  let form = $state({ key_alias: '', models: [], max_budget: '', budget_duration: '', duration: '', rpm_limit: '', tpm_limit: '' })

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
      <button onclick={() => navigator.clipboard?.writeText(newKey)}>Copy</button>
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
      <div class="row"><button class="primary" onclick={create} disabled={busy}>Create</button><button onclick={() => showCreate = false}>Cancel</button></div>
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
</style>
