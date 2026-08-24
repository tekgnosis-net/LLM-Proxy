<script>
  import { api } from '../lib/api.js'
  import { FALLBACK_PROVIDERS } from '../lib/providers.js'
  let { store } = $props()
  let err = $state(''), showAdd = $state(false)
  let form = $state({ credential_name: '', provider: 'openai', api_key: '' })
  let editingName = $state(null)
  let providers = $state(FALLBACK_PROVIDERS)
  // Attempt to load the full catalog provider list on mount (fallback stays on error)
  $effect(() => {
    api.catalogProviders().then(ps => { if (Array.isArray(ps) && ps.length) providers = ps }).catch(() => {})
  })
  let creds = $derived(store.itemsOfKind('credential'))
  function editCred(item) {
    form = { credential_name: item.name, provider: item.data?.provider || 'openai', api_key: '' }
    editingName = item.name; showAdd = true; err = ''
  }
  async function save() {
    err = ''
    if (!form.credential_name) return
    if (!editingName && !form.api_key) return
    const ok = await store.stageItem('credential', form.credential_name, {
      provider: form.provider,
      api_key: form.api_key,
    })
    if (ok) { form = { credential_name: '', provider: 'openai', api_key: '' }; editingName = null; showAdd = false }
    else err = store.error || 'Failed to stage credential.'
  }
  async function del(name) {
    if (!confirm(`Delete "${name}"? It will be removed from config.yaml on Apply.`)) return
    err = ''
    const ok = await store.deleteItem('credential', name)
    if (!ok) err = store.error || 'Failed to stage deletion.'
  }
  async function undo(name) {
    err = ''
    const ok = await store.discard('credential', name)
    if (!ok) err = store.error || 'Failed to discard.'
  }
</script>
<div class="page">
  <header>
    <h1>Provider Keys</h1>
    <button class="primary" onclick={() => { editingName = null; showAdd = !showAdd }}>＋ Add key</button>
  </header>
  {#if err}<div class="banner err">{err}</div>{/if}
  <p class="hint">Keys are encrypted at rest and written into <code>config.yaml</code> on Apply (secrets redacted here). Saving or deleting a key stages a change — click <strong>Apply</strong> to activate; Discard reverts staged changes.</p>
  {#if showAdd}
    <div class="card add">
      <h3>{editingName ? 'Edit key' : 'Add key'}</h3>
      <label>Name <input bind:value={form.credential_name} placeholder="e.g. openai_prod" disabled={!!editingName} /></label>
      <label>Provider
        <select bind:value={form.provider}>
          {#each providers as p}
            <option value={p.provider}>{p.display_name || p.provider}</option>
          {/each}
        </select>
      </label>
      <label>API key <input type="password" bind:value={form.api_key} placeholder={editingName ? 'leave blank to keep the current key' : 'sk-…'} /></label>
      <div class="row">
        <button class="primary" onclick={save} disabled={store.saving || !form.credential_name || (!editingName && !form.api_key)}>Save key</button>
        <button onclick={() => { showAdd = false; editingName = null }}>Cancel</button>
      </div>
    </div>
  {/if}
  <div class="card">
    {#if creds.length === 0}
      <p class="empty">No provider keys yet.</p>
    {:else}
      <table>
        <thead><tr><th>Name</th><th>Provider</th><th>Value</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {#each creds as k}
            {@const isDeleted = k.flag === 'deleted'}
            {@const isNew = k.flag === 'new'}
            {@const isChanged = k.flag === 'changed'}
            <tr class:row-deleted={isDeleted} class:row-new={isNew} class:row-changed={isChanged}>
              <td class:strikethrough={isDeleted}>{k.name}</td>
              <td class:strikethrough={isDeleted}>{k.data?.provider || '—'}</td>
              <td class:strikethrough={isDeleted}><code>***</code></td>
              <td>
                {#if isNew}<span class="tag tag-new">new</span>
                {:else if isChanged}<span class="tag tag-changed">changed</span>
                {:else if isDeleted}<span class="tag tag-deleted">deleted</span>
                {/if}
              </td>
              <td>
                {#if isDeleted}
                  <button class="undo" onclick={() => undo(k.name)} disabled={store.saving}>Undo</button>
                {:else}
                  <button class="secondary" onclick={() => editCred(k)} disabled={store.saving}>Edit</button>
                  <button class="danger" onclick={() => del(k.name)} disabled={store.saving}>Delete</button>
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
  .page { padding: 24px 30px; max-width: 760px }
  header { display: flex; justify-content: space-between; align-items: center }
  .card { border: 1px solid var(--border, rgba(0,0,0,.08)); border-radius: 12px; padding: 16px; margin-top: 14px; background: var(--card, #fff) }
  .card.add { display: flex; flex-direction: column; gap: 10px; max-width: 420px }
  .card.add h3 { margin: 0 0 4px; font-size: 15px; font-weight: 600 }
  label { display: flex; flex-direction: column; font-size: 13px; gap: 4px; color: var(--muted, #3a3a3c) }
  input, select { padding: 8px; border: 1px solid var(--border, #ccc); border-radius: 8px; font: inherit; color: var(--text) }
  input:disabled { background: var(--chip); color: var(--muted, #6e6e73); cursor: not-allowed }
  table { width: 100%; border-collapse: collapse }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border, rgba(0,0,0,.06)); font-size: 14px }
  .row { display: flex; gap: 8px }
  button { padding: 8px 12px; border: 1px solid var(--border, #ccc); border-radius: 8px; background: var(--card, #fff); font: inherit; cursor: pointer; color: var(--text) }
  button.primary { background: #0a84ff; color: #fff; border: 0 }
  button.secondary { color: #0a84ff; border-color: #b3d4ff }
  button.danger { color: #ff3b30; border-color: #ffd0cc }
  button.undo { color: #ff9f0a; border-color: #ffe5b0 }
  button:disabled { opacity: .5 }
  .banner.err { background: #ffeceb; color: #c0271d; padding: 10px 12px; border-radius: 8px; font-size: 13px }
  .hint { font-size: 12px; color: var(--muted, #6e6e73) }
  .empty { color: var(--muted, #6e6e73) }
  /* flag row colours */
  .row-new td { background: rgba(10, 132, 255, 0.06) }
  .row-changed td { background: rgba(255, 159, 10, 0.08) }
  .row-deleted td { background: rgba(255, 59, 48, 0.06) }
  .strikethrough { text-decoration: line-through; color: var(--muted, #6e6e73) }
  /* status tags */
  .tag { display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 10px; letter-spacing: .02em }
  .tag-new { background: #e5f0ff; color: #0a84ff }
  .tag-changed { background: #fff4e0; color: #b36b00 }
  .tag-deleted { background: #ffeceb; color: #c0271d }
</style>
