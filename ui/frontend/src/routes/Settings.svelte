<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let { store, theme, setTheme } = $props()

  // Change admin password
  let cpOld = $state(''), cpNew = $state(''), cpConfirm = $state('')
  let cpBusy = $state(false), cpMsg = $state(''), cpErr = $state('')
  let cpDisabled = $derived(cpBusy || cpNew.length < 8 || cpNew !== cpConfirm)
  async function changePassword() {
    cpBusy = true; cpMsg = ''; cpErr = ''
    try {
      await api.post('/api/auth/change-password', { old_password: cpOld, new_password: cpNew })
      cpMsg = 'Password changed'; cpOld = ''; cpNew = ''; cpConfirm = ''
    } catch (e) {
      cpErr = e.message || 'Failed to change password'
    } finally { cpBusy = false }
  }

  // Passthrough editor
  let ptYaml = $state('')
  let ptErr = $state(''), ptMsg = $state(''), ptBusy = $state(false)
  async function loadPassthrough() {
    try { const r = await api.passthroughGet(); ptYaml = r.yaml ?? '' } catch (e) { ptErr = e.message }
  }
  async function savePassthrough() {
    ptBusy = true; ptErr = ''; ptMsg = ''
    try {
      await api.passthroughPut(ptYaml)
      ptMsg = 'Staged. Click Apply to make it live.'
      await store.load()
    } catch (e) {
      ptErr = e.status === 422 ? `Rejected: ${e.message}` : e.message
    } finally { ptBusy = false }
  }

  // Global health-check interval (general_settings.health_check_interval, seconds)
  let hcInterval = $state('')
  let hcMsg = $state(''), hcErr = $state(''), hcBusy = $state(false)
  function loadHcInterval() {
    const it = store.itemsOfKind('general_setting').find(i => i.name === 'health_check_interval')
    hcInterval = (it && it.data != null) ? String(it.data) : ''
  }
  async function saveHcInterval() {
    hcBusy = true; hcMsg = ''; hcErr = ''
    try {
      const n = parseInt(hcInterval, 10)
      if (!Number.isFinite(n) || n < 30) { hcErr = 'Enter a whole number of seconds ≥ 30'; return }
      await store.stageItem('general_setting', 'health_check_interval', n)
      hcMsg = 'Staged. Click Apply to make it live (settings change → brief restart).'
    } catch (e) { hcErr = e.message }
    finally { hcBusy = false }
  }

  let hotBusy = $state(false), hotMsg = $state(''), hotErr = $state('')
  async function prepareHotApply() {
    hotBusy = true; hotMsg = ''; hotErr = ''
    try { const r = await api.prepareHotApply(); hotMsg = r.next } catch (e) { hotErr = e.message }
    finally { hotBusy = false }
  }
  function confirmPrepareHotApply() {
    if (confirm('This empties models from config.yaml and restarts the proxy (brief downtime). The model list is then served from the database. Continue?')) prepareHotApply()
  }

  let catStatus = $state(null), catBusy = $state(false), catMsg = $state('')
  onMount(() => {
    api.catalogStatus().then(s => catStatus = s).catch(() => {})
    loadPassthrough()
    loadHcInterval()
  })
  async function syncCatalog() {
    catBusy = true; catMsg = ''
    try {
      const r = await api.catalogSync()
      catMsg = `Synced ${r.models} models, ${r.providers} providers`
      catStatus = await api.catalogStatus()
    } catch (e) { catMsg = e.message }
    finally { catBusy = false }
  }
</script>
<div class="page"><h1>Settings</h1>
  <div class="card"><h2>Appearance</h2>
    <label class="row"><input type="checkbox" checked={theme==='dark'} onchange={(e) => setTheme(e.target.checked ? 'dark' : 'light')} /> Dark mode</label>
  </div>
  <div class="card"><h2>Raw / advanced (passthrough)</h2>
    <p class="hint">Additional YAML merged verbatim into config.yaml on Apply. Use for settings not covered by the UI (e.g. callbacks, guardrails). Must be valid YAML; top-level keys only.</p>
    <textarea bind:value={ptYaml} rows="10" spellcheck="false" placeholder="# e.g.&#10;callbacks:&#10;  - langfuse"></textarea>
    <div class="row" style="margin-top:8px">
      <button onclick={savePassthrough} disabled={ptBusy}>{ptBusy ? 'Saving…' : 'Save passthrough'}</button>
    </div>
    {#if ptErr}<div class="banner err">{ptErr}</div>{/if}
    {#if ptMsg}<div class="banner ok">{ptMsg}</div>{/if}
    {#if store.applying}<div class="banner info">{store.storeModelInDb ? 'Applying changes…' : 'Applying… restarting the proxy (~25s)'}</div>{/if}
  </div>
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
  <div class="card"><h2>Export config (ui_config)</h2>
    <p class="hint">Download a snapshot of the UI's source-of-truth config (models, settings, encrypted credentials). This is the reproducibility/backup artifact — restore it on a fresh stack. Credentials are exported encrypted (restoreable only with the same SESSION_SECRET).</p>
    <div class="row">
      <a class="btn" href={api.exportConfigUrl} download>⬇ Export ui_config.json</a>
    </div>
  </div>
  <div class="card"><h2>LiteLLM catalog</h2>
    <p class="hint">Model prices/context + provider endpoints, synced from the LiteLLM repo and used to auto-fill Models.</p>
    {#if catStatus}<p>Last synced: <strong>{catStatus.last_synced ? new Date(catStatus.last_synced).toLocaleString() : 'never'}</strong>
      · {catStatus.models} models · {catStatus.providers} providers{catStatus.last_error ? ` · last error: ${catStatus.last_error}` : ''}</p>{/if}
    <button onclick={syncCatalog} disabled={catBusy}>{catBusy ? 'Syncing…' : 'Sync now'}</button>
    {#if catMsg}<div class="banner ok">{catMsg}</div>{/if}
  </div>
  <div class="card"><h2>Enable hot-apply (model changes without restart)</h2>
    <p class="hint">One-time migration. Step 1 empties the model list from config.yaml and restarts the proxy (brief downtime). Then set <code>STORE_MODEL_IN_DB=true</code> in <code>.env</code>, run <code>docker compose up -d</code>, and click Apply to fill the model DB. After this, model add/edit/delete apply instantly.</p>
    <div class="row"><button onclick={confirmPrepareHotApply} disabled={hotBusy}>{hotBusy ? 'Preparing…' : 'Step 1: Prepare (empty config models + restart)'}</button></div>
    {#if hotErr}<div class="banner err">{hotErr}</div>{/if}
    {#if hotMsg}<div class="banner ok">{hotMsg}</div>{/if}
  </div>
  <div class="card"><h2>Change admin password</h2>
    <p class="hint">Updates the admin login password. The new password must be at least 8 characters.</p>
    <div class="pw-fields">
      <label>Current password<input type="password" bind:value={cpOld} placeholder="Current password" autocomplete="current-password" /></label>
      <label>New password<input type="password" bind:value={cpNew} placeholder="New password (min 8 chars)" autocomplete="new-password" /></label>
      <label>Confirm new password<input type="password" bind:value={cpConfirm} placeholder="Confirm new password" autocomplete="new-password" /></label>
    </div>
    <div class="row" style="margin-top:10px">
      <button onclick={changePassword} disabled={cpDisabled}>{cpBusy ? 'Saving…' : 'Save'}</button>
    </div>
    {#if cpErr}<div class="banner err">{cpErr}</div>{/if}
    {#if cpMsg}<div class="banner ok">{cpMsg}</div>{/if}
  </div>
</div>
<style>
  .page{padding:24px 30px;max-width:680px}h2{font-size:15px;margin:0 0 10px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card)}
  label.row{display:flex;align-items:center;gap:8px;color:var(--text)}
  .row{display:flex;gap:10px;align-items:center}
  .btn{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);cursor:pointer;text-decoration:none;font-size:14px}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px;margin-top:10px}.banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:var(--muted)}
  button{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  textarea{width:100%;box-sizing:border-box;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px;padding:8px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);resize:vertical}
  .pw-fields{display:flex;flex-direction:column;gap:8px}
  .pw-fields label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text)}
  .pw-fields input{padding:7px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font:inherit;font-size:14px}
  label.hc{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text);max-width:220px}
</style>
