<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'

  let status = $state(null), list = $state(null), settings = $state(null)
  let err = $state(''), msg = $state(''), busyTier = $state(''), restoring = $state(false)
  let recoverySteps = $state(null), mergeResult = $state(null), preview = $state(null)
  const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

  async function load() {
    err = ''
    try {
      [status, list, settings] = await Promise.all([api.backupStatus(), api.backupList(), api.backupSettings()])
    } catch (e) { err = e.message }
  }
  onMount(load)

  function fmtBytes(n) {
    if (n == null) return '—'
    if (n < 1024) return `${n} B`
    if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
    if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`
    return `${(n / 1073741824).toFixed(2)} GB`
  }
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString() : '—'

  async function saveTier(tier) {
    err = ''; msg = ''
    try {
      settings = await api.saveBackupSettings({ [tier]: settings[tier] })
      msg = `${tier} schedule saved`
      status = await api.backupStatus()
    } catch (e) { err = e.message }
  }

  async function runNow(tier) {
    busyTier = tier; err = ''; msg = ''
    try {
      const r = await api.backupRun(tier)
      msg = r.ok ? `Backed up → ${r.path} (${fmtBytes(r.bytes)})` : `Backup failed: ${r.error}`
      await load()
    } catch (e) { err = e.message } finally { busyTier = '' }
  }

  async function doRollback(id) {
    err = ''; preview = null
    try { preview = { id, ...(await api.rollbackPreview(id)) } } catch (e) { err = e.message }
  }
  async function confirmRollback() {
    if (prompt(`Replace the current master config with ${preview.id}?\nStaged changes are discarded.\nType ROLLBACK to confirm`) !== 'ROLLBACK') return
    restoring = true; err = ''; msg = ''
    try {
      const r = await api.backupRollback(preview.id)
      msg = `Rolled back — models: +${r.models?.added ?? 0}/~${r.models?.updated ?? 0}/−${r.models?.deleted ?? 0}, restart: ${r.restart}`
      preview = null; await load()
    } catch (e) { err = e.message } finally { restoring = false }
  }

  async function doRecover(id) {
    if (prompt(`FULL RECOVERY from ${id}?\nStops the proxy ~1 min and replaces config, models, keys and teams.\nUsage logs are untouched.\nType RECOVER to confirm`) !== 'RECOVER') return
    restoring = true; err = ''; msg = ''; recoverySteps = null
    try {
      const r = await api.backupRecover(id)
      recoverySteps = r.steps
      msg = r.ok ? 'Full recovery complete' : 'Full recovery finished with errors — see steps'
      await load()
    } catch (e) { err = e.message } finally { restoring = false }
  }

  async function doMerge(id) {
    if (prompt(`Merge usage rows from ${id === 'all' ? 'ALL logs backups' : id} into the database?\nExisting rows are never modified.\nType MERGE to confirm`) !== 'MERGE') return
    restoring = true; err = ''; msg = ''; mergeResult = null
    try { mergeResult = await api.backupRestoreLogs(id); await load() }
    catch (e) { err = e.message } finally { restoring = false }
  }

  async function doDelete(id) {
    if (!confirm(`Delete ${id}? This cannot be undone.`)) return
    try { await api.backupDelete(id); await load() } catch (e) { err = e.message }
  }
</script>

<div class="wrap">
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if msg}<div class="banner ok">{msg}</div>{/if}
  {#if status?.master_empty_live_nonempty}
    <div class="banner err">Master config is empty while LiteLLM serves {status.live_models} models —
      this usually means the ui_* tables were lost. Restore a config backup below; do NOT Resync.</div>
  {/if}

  {#if status}
    <div class="card"><h2>Status</h2>
      <table><thead><tr><th>Tier</th><th>Last good backup</th><th>Next run</th><th>State</th><th></th></tr></thead><tbody>
        {#each ['config', 'logs'] as t}
          {@const s = status.tiers[t]}
          <tr>
            <td>{t}</td>
            <td>{s.last_ok ? `${fmtTime(s.last_ok.finished_at)} · ${fmtBytes(s.last_ok.bytes)}` : 'never'}</td>
            <td>{s.enabled ? fmtTime(s.next_run) : 'disabled'}</td>
            <td>{s.running ? 'running…' : s.stale ? '⚠ stale' : s.last_error ? `⚠ last error: ${s.last_error}` : 'ok'}</td>
            <td><button onclick={() => runNow(t)} disabled={busyTier === t}>{busyTier === t ? 'Backing up…' : 'Back up now'}</button></td>
          </tr>
        {/each}
      </tbody></table>
    </div>
  {/if}

  {#if settings}
    {#each ['config', 'logs'] as t}
      <div class="card"><h2>{t === 'config' ? 'Config backups' : 'Logs backups (usage export)'}</h2>
        <p class="hint">{t === 'config'
          ? 'Full dump of configuration, models, MCP servers, keys and teams (usage tables excluded).'
          : 'Incremental CSV slices of usage tables — with request logging on, these carry full request/response bodies (your dataset export). Retention 0 keeps slices forever.'}</p>
        <div class="row">
          <label class="chk"><input type="checkbox" bind:checked={settings[t].enabled} /> Enabled</label>
          <select bind:value={settings[t].frequency.kind}>
            <option value="daily">Daily</option><option value="weekly">Weekly</option>
            <option value="every_n_days">Every N days</option>
          </select>
          {#if settings[t].frequency.kind === 'weekly'}
            <select bind:value={settings[t].frequency.weekday}>
              {#each WEEKDAYS as d, i}<option value={i}>{d}</option>{/each}
            </select>
          {/if}
          {#if settings[t].frequency.kind === 'every_n_days'}
            <label>N <input class="num" type="number" min="2" max="365" bind:value={settings[t].frequency.n} /></label>
          {/if}
          <label>at <input class="hhmm" type="time" bind:value={settings[t].time} /></label>
          <label>retain <input class="num" type="number" min="0" max="3650" bind:value={settings[t].retention_days} /> days</label>
          <button onclick={() => saveTier(t)}>Save</button>
        </div>
      </div>
    {/each}
  {/if}

  {#if list}
    <div class="card"><h2>Backups</h2>
      {#if !list.backups.length}<p class="hint">No backups yet.</p>{:else}
      <table><thead><tr><th>Backup</th><th>Taken</th><th>Size</th><th>Contents</th><th>Actions</th></tr></thead><tbody>
        {#each list.backups as b}
          <tr>
            <td class="mono">{b.id}</td><td>{fmtTime(b.taken_at)}</td><td>{fmtBytes(b.bytes)}</td>
            <td>{Object.entries(b.summary || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}</td>
            <td class="actions">
              {#if b.tier === 'config'}
                <button onclick={() => doRollback(b.id)} disabled={restoring}>Rollback config</button>
                <button class="danger" onclick={() => doRecover(b.id)} disabled={restoring}>Full recovery</button>
              {:else}
                <button onclick={() => doMerge(b.id)} disabled={restoring}>Restore (merge)</button>
              {/if}
              {#each b.files.filter(f => f !== 'manifest.json') as f}
                <a class="dl" href={api.backupDownloadUrl(`${b.id}/${f}`)} download title={f}>⬇ {f}</a>
              {/each}
              <button class="danger" onclick={() => doDelete(b.id)} disabled={restoring}>Delete</button>
            </td>
          </tr>
        {/each}
      </tbody></table>
      {#if list.backups.some(b => b.tier === 'logs')}
        <button onclick={() => doMerge('all')} disabled={restoring}>Restore ALL logs slices (merge)</button>
      {/if}
      {/if}
    </div>

    <div class="card"><h2>Apply snapshots</h2>
      <p class="hint">The master config is snapshotted after every successful Apply (last 50 kept).</p>
      {#if !list.snapshots.length}<p class="hint">No snapshots yet.</p>{:else}
      <table><tbody>
        {#each list.snapshots as s}
          <tr><td class="mono">{s.id}</td><td>{fmtBytes(s.bytes)}</td>
            <td><button onclick={() => doRollback(s.id)} disabled={restoring}>Rollback config</button>
                <button class="danger" onclick={() => doDelete(s.id)} disabled={restoring}>Delete</button></td></tr>
        {/each}
      </tbody></table>
      {/if}
    </div>

    <div class="card"><h2>Run history</h2>
      {#if !(list.runs || []).length}<p class="hint">No runs yet.</p>{:else}
      <table><thead><tr><th>Tier</th><th>Started</th><th>Status</th><th>Result</th></tr></thead><tbody>
        {#each list.runs as r}
          <tr><td>{r.tier}</td><td>{fmtTime(r.started_at)}</td>
            <td class:red={r.status === 'error'}>{r.status}</td>
            <td>{r.error || (r.path ? `${r.path} · ${fmtBytes(r.bytes)}` : '—')}</td></tr>
        {/each}
      </tbody></table>
      {/if}
    </div>
  {/if}

  {#if preview}
    <div class="card"><h2>Rollback preview — {preview.id}</h2>
      {#if preview.undecryptable?.length}
        <div class="banner err">Cannot decrypt with the current secret: {preview.undecryptable.join(', ')} — rollback refused.</div>
      {:else}
        <p>+{preview.added.length} added · −{preview.removed.length} removed · ~{preview.changed.length} changed
          {#if preview.restart_kinds_changed} · includes settings → proxy restart (~25s){/if}</p>
        <ul class="diff">
          {#each preview.added as i}<li class="green">+ {i.kind}/{i.name}</li>{/each}
          {#each preview.removed as i}<li class="red">− {i.kind}/{i.name}</li>{/each}
          {#each preview.changed as i}<li>~ {i.kind}/{i.name}</li>{/each}
        </ul>
        <button class="danger" onclick={confirmRollback} disabled={restoring}>{restoring ? 'Rolling back…' : 'Roll back to this'}</button>
      {/if}
      <button onclick={() => preview = null}>Close</button>
    </div>
  {/if}

  {#if recoverySteps}
    <div class="card"><h2>Recovery steps</h2>
      <table><tbody>{#each recoverySteps as s}
        <tr><td>{s.step}</td><td class:red={s.status === 'error'} class:green={s.status === 'ok'}>{s.status}</td><td>{s.detail || ''}</td></tr>
      {/each}</tbody></table>
    </div>
  {/if}

  {#if mergeResult}
    <div class="card"><h2>Logs merge result</h2>
      <table><thead><tr><th>Table</th><th>Inserted</th><th>Skipped (already present)</th><th>Notes</th></tr></thead><tbody>
        {#each Object.entries(mergeResult.tables) as [t, v]}
          <tr><td class="mono">{t}</td><td>{v.inserted}</td><td>{v.skipped}</td>
            <td>{v.error || (v.dropped_columns?.length ? `dropped: ${v.dropped_columns.join(', ')}` : '')}</td></tr>
        {/each}
      </tbody></table>
    </div>
  {/if}
</div>

<style>
  .wrap{max-width:900px}h2{font-size:15px;margin:0 0 10px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card)}
  table{width:100%;border-collapse:collapse}th{text-align:left;font-size:12px;color:var(--muted);padding:6px 8px}
  td{padding:6px 8px;border-bottom:1px solid var(--border);font-size:13px;vertical-align:top}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .chk{display:flex;align-items:center;gap:6px}
  .mono{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:12px}
  .num{width:64px}.hhmm{width:110px}
  input,select{padding:6px 8px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font:inherit}
  button{padding:7px 11px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  button.danger{color:#ff3b30;border-color:#ffd0cc}button:disabled{opacity:.5}
  .dl{font-size:12px;margin-right:6px;text-decoration:none;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:2px 6px}
  .actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px;margin-top:10px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}
  .hint{font-size:12px;color:var(--muted)}
  .diff{font-family:ui-monospace,monospace;font-size:12px;max-height:220px;overflow:auto;padding-left:16px}
  .green{color:#1d7a33}.red{color:#c0271d}td.red{color:#c0271d}td.green{color:#1d7a33}
</style>
