<script>
  import { api } from '../lib/api.js'
  let { store, theme, setTheme } = $props()
  let importErr = $state(''), importMsg = $state('')
  async function onImport(e) {
    const file = e.target.files?.[0]; if (!file) return
    importErr = ''; importMsg = ''
    let cfg
    try { cfg = JSON.parse(await file.text()) }
    catch { importErr = 'File must be valid JSON (YAML import requires the yaml package — not bundled)'; return }
    if (!store.config) await store.load()
    try { await api.putConfig(cfg); importMsg = 'Imported & applied.'; await store.load() }
    catch (er) { importErr = (er.status === 422 ? 'Rejected: ' : er.status === 409 ? 'Reload failed, rolled back: ' : '') + er.message }
  }
</script>
<div class="page"><h1>Settings</h1>
  <div class="card"><h2>Appearance</h2>
    <label class="row"><input type="checkbox" checked={theme==='dark'} onchange={(e) => setTheme(e.target.checked ? 'dark' : 'light')} /> Dark mode</label>
  </div>
  <div class="card"><h2>Export / Import config</h2>
    <p class="hint">Download a snapshot of <code>config.yaml</code>, or import one (validated + applied via safe-apply). Import accepts JSON only.</p>
    <div class="row">
      <a class="btn" href={api.exportConfigUrl} download>⬇ Export config.yaml</a>
      <label class="btn">⬆ Import…<input type="file" accept=".json" onchange={onImport} style="display:none" /></label>
    </div>
    {#if importErr}<div class="banner err">{importErr}</div>{/if}
    {#if importMsg}<div class="banner ok">{importMsg}</div>{/if}
    {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}
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
</style>
