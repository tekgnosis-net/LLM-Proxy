<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let info = $state(null), err = $state('')
  onMount(async () => { try { info = await api.cacheInfo() } catch (e) { err = e.message } })
</script>
<div class="page"><h1>Caching <span class="sub">read-only</span></h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if info}
    <div class="card">
      <div class="row"><span>Status</span><strong>{info.enabled ? 'Enabled' : 'Disabled'}</strong></div>
      <div class="row"><span>Type</span><strong>{info.type || '—'}</strong></div>
      <div class="row"><span>Backend</span><strong>{info.host} : {info.port}</strong></div>
      <div class="row"><span>TTL</span><strong>{info.ttl != null ? info.ttl + ' s' : 'default (600 s)'}</strong></div>
      <p class="hint">The cache backend is provisioned in <code>docker-compose.yml</code> (the <code>valkey</code>
      service, reached via Docker DNS at <code>{info.host}:{info.port}</code>). To change it, edit
      <code>docker-compose.yml</code> / <code>config.yaml</code> — it isn't editable here by design.</p>
    </div>
  {/if}
</div>
<style>
  .page{padding:24px 30px;max-width:620px}.sub{font-size:13px;color:var(--muted,#6e6e73);font-weight:400}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;margin-top:14px;background:var(--card,#fff)}
  .row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border,rgba(0,0,0,.06));font-size:14px}
  .hint{font-size:12px;color:var(--muted,#6e6e73);margin-top:12px}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;font-size:13px}
</style>
