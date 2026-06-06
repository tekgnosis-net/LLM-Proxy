<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let d = $state(null), err = $state(''), busy = $state(false), result = $state(null)
  async function load() { try { d = await api.housekeeping() } catch (e) { err = e.message } }
  onMount(load)
  async function run() {
    if (!confirm('Run maintenance now? This deletes spend logs older than the retention window and expired keys.')) return
    busy = true; err = ''; result = null
    try { result = await api.runHousekeeping(); await load() } catch (e) { err = e.message } finally { busy = false }
  }
</script>
<div class="page"><h1>DB Housekeeping</h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if d}
    <div class="card"><h2>Database</h2>
      <p>Size: <strong>{d.stats.db_size}</strong></p>
      <table><tbody>{#each Object.entries(d.stats.row_counts) as [t, n]}<tr><td>{t}</td><td>{n ?? '—'} rows</td></tr>{/each}</tbody></table>
    </div>
    <div class="card"><h2>Maintenance</h2>
      <p>Scheduled cron: <strong>{d.settings.enabled ? `every ${d.settings.interval_hours}h` : 'disabled'}</strong>
        · retention <strong>{d.settings.retention_days} days</strong>
        · delete expired keys: <strong>{d.settings.delete_expired_keys ? 'yes' : 'no'}</strong></p>
      <p class="hint">Enable/tune the cron via <code>HOUSEKEEPING_*</code> env vars. "Run now" applies the same retention immediately.</p>
      <button class="danger" onclick={run} disabled={busy}>{busy ? 'Running…' : 'Run maintenance now'}</button>
      {#if result}<div class="banner ok">Trimmed {result.trimmed_spend_logs} spend logs{result.deleted_expired_keys != null ? `, deleted ${result.deleted_expired_keys} expired keys` : ''} (retention {result.retention_days}d).</div>{/if}
    </div>
  {/if}
</div>
<style>
  .page{padding:24px 30px;max-width:760px}h2{font-size:15px;margin:0 0 10px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card)}
  table{width:100%;border-collapse:collapse}td{padding:6px 8px;border-bottom:1px solid var(--border);font-size:14px}
  button{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  button.danger{color:#ff3b30;border-color:#ffd0cc}button:disabled{opacity:.5}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px;margin-top:10px}.banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}
  .hint{font-size:12px;color:var(--muted)}
</style>
