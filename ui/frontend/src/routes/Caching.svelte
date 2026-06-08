<script>
  let { store } = $props()
  const cache = $derived(store.itemNamed('litellm_setting', 'cache')?.data)
  const cp = $derived(store.itemNamed('litellm_setting', 'cache_params')?.data || {})
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
</div>
<style>
  .page{padding:24px 30px;max-width:620px}.sub{font-size:13px;color:var(--muted,#6e6e73);font-weight:400}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;margin-top:14px;background:var(--card,#fff)}
  .row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border,rgba(0,0,0,.06));font-size:14px}
  .enabled{color:#1a8c1a}.disabled{color:var(--muted,#6e6e73)}
  .hint{font-size:12px;color:var(--muted,#6e6e73);margin-top:12px}
</style>
