<script>
  import { onMount, onDestroy } from 'svelte'
  import { api } from '../lib/api.js'
  let { store } = $props()
  const cache = $derived(store.itemNamed('litellm_setting', 'cache')?.data)
  const cp = $derived(store.itemNamed('litellm_setting', 'cache_params')?.data || {})
  let stats = $state(null), updatedAt = $state(0); let timer
  async function refresh() {
    try { stats = await api.cacheStats(); updatedAt = Date.now() }
    catch (e) { stats = { connected: false, error: e.message } }
  }
  function pct(x) { return x == null ? '—' : (x * 100).toFixed(0) + '%' }
  function dur(s) {
    if (s == null) return '—'
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600)
    return d ? `${d}d ${h}h` : `${h}h`
  }
  function ago(ts) {
    if (!ts) return ''
    const s = Math.round((Date.now() - ts) / 1000)
    return `updated ${s}s ago`
  }
  onMount(() => { refresh(); timer = setInterval(refresh, 10000) })
  onDestroy(() => clearInterval(timer))
</script>
<div class="page"><h1>Caching <span class="sub">read-only</span></h1>
  <div class="card">
    <div class="row"><span>Status</span><strong class:enabled={cache} class:disabled={!cache}>{cache ? 'Enabled' : 'Disabled'}</strong></div>
    <div class="row"><span>Type</span><strong>{cp.type || '—'}</strong></div>
    <div class="row"><span>Backend</span><strong>valkey : 6379</strong></div>
    <div class="row"><span>TTL</span><strong>{cp.ttl != null ? cp.ttl + ' s' : 'default (600 s)'}</strong></div>
    <p class="hint">The cache backend is provisioned in <code>docker-compose.yml</code> (the <code>valkey</code>
    service, reached via Docker DNS). The <code>host</code> and <code>port</code> values in
    <code>cache_params</code> are <code>os.environ/</code> references resolved by Docker at runtime
    (effective: <code>valkey:6379</code>). To change the backend, edit
    <code>docker-compose.yml</code> — it isn't editable here by design.</p>
  </div>

  <h2 class="section-head">Live Stats</h2>
  <div class="card stats-card">
    <div class="stats-header">
      <span class="dot" class:dot-on={stats?.connected} class:dot-off={stats != null && !stats?.connected}></span>
      <span class="backend-label">{stats?.backend ?? '…'}</span>
      {#if stats?.connected}<span class="rtt">{stats.rtt_ms} ms RTT</span>{/if}
      <button class="refresh-btn" onclick={refresh}>Refresh</button>
      {#if updatedAt}<span class="ago">{ago(updatedAt)}</span>{/if}
    </div>

    {#if stats == null}
      <p class="hint">Loading…</p>
    {:else if !stats.connected}
      <div class="err-row"><span class="dot dot-off"></span> Disconnected — {stats.error ?? 'unknown error'}</div>
    {:else}
      <div class="row"><span>Used memory</span><strong>{stats.used_memory_human ?? '—'}</strong></div>
      <div class="row"><span>Peak memory</span><strong>{stats.used_memory_peak_human ?? '—'}</strong></div>
      <div class="row"><span>Cache hits</span><strong>{stats.keyspace_hits ?? '—'}</strong></div>
      <div class="row"><span>Cache misses</span><strong>{stats.keyspace_misses ?? '—'}</strong></div>
      <div class="row"><span>Hit rate</span><strong>{pct(stats.hit_rate)}</strong></div>
      <div class="row"><span>Evictions</span><strong>{stats.evicted_keys ?? '—'}</strong></div>
      <div class="row"><span>Key count</span><strong>{stats.db_keys ?? '—'}</strong></div>
      <div class="row"><span>Connected clients</span><strong>{stats.connected_clients ?? '—'}</strong></div>
      <div class="row"><span>Uptime</span><strong>{dur(stats.uptime_in_seconds)}</strong></div>
    {/if}
  </div>
</div>
<style>
  .page{padding:24px 30px;max-width:620px}.sub{font-size:13px;color:var(--muted,#6e6e73);font-weight:400}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;margin-top:14px;background:var(--card,#fff)}
  .row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border,rgba(0,0,0,.06));font-size:14px}
  .enabled{color:#1a8c1a}.disabled{color:var(--muted,#6e6e73)}
  .hint{font-size:12px;color:var(--muted,#6e6e73);margin-top:12px}
  .section-head{font-size:15px;font-weight:600;margin-top:24px;margin-bottom:0}
  .stats-header{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
  .backend-label{font-size:13px;font-weight:500}
  .rtt{font-size:12px;color:var(--muted,#6e6e73)}
  .ago{font-size:11px;color:var(--muted,#6e6e73);margin-left:auto}
  .dot{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--muted,#aaa);flex-shrink:0}
  .dot-on{background:#1a8c1a}.dot-off{background:#ff3b30}
  .refresh-btn{font-size:12px;padding:3px 10px;border:1px solid var(--border,rgba(0,0,0,.12));border-radius:6px;background:var(--card,#fff);cursor:pointer;color:inherit}
  .refresh-btn:hover{background:var(--border,rgba(0,0,0,.06))}
  .err-row{display:flex;align-items:center;gap:8px;font-size:14px;color:#ff3b30;padding:6px 0}
  .stats-card .row:last-child{border-bottom:none}
</style>
