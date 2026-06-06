<script>
  import { onMount } from 'svelte'
  let { store } = $props()
  let enabled = $state(true), type = $state('redis'), host = $state(''), port = $state(''), ttl = $state('')
  onMount(async () => { if (!store.config) await store.load(); sync() })
  function sync() {
    const ls = store.config?.litellm_settings ?? {}; const cp = ls.cache_params ?? {}
    enabled = ls.cache ?? false; type = cp.type ?? 'redis'; host = cp.host ?? ''; port = cp.port ?? ''; ttl = cp.ttl ?? ''
  }
  async function save() {
    const ls = { ...(store.config?.litellm_settings ?? {}) }
    ls.cache = enabled
    const cp = { ...(ls.cache_params ?? {}), type, host, port }
    if (ttl !== '' && ttl != null) cp.ttl = Number(ttl); else delete cp.ttl
    delete cp.ssl; delete cp.ssl_check_hostname   // guardrail: never emit ssl
    ls.cache_params = cp
    const ok = await store.saveSection('litellm_settings', ls); if (ok) sync()
  }
</script>
<div class="page"><h1>Caching</h1>
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}
  <div class="card">
    <label class="row"><input type="checkbox" bind:checked={enabled} /> Enable response cache (Valkey/Redis)</label>
    <label>Type <input bind:value={type} placeholder="redis" /></label>
    <label>Host <input bind:value={host} placeholder="os.environ/REDIS_HOST" /></label>
    <label>Port <input bind:value={port} placeholder="os.environ/REDIS_PORT" /></label>
    <label>TTL (seconds) <input type="number" min="0" bind:value={ttl} placeholder="default 600" /></label>
    <p class="hint">The UI never writes an <code>ssl</code> key — LiteLLM bug #10949 makes any ssl key hang against plain Valkey.</p>
    <div class="row"><button class="primary" onclick={save} disabled={store.applying}>Save &amp; apply</button><button onclick={sync} disabled={store.applying}>Reset</button></div>
  </div>
</div>
<style>
  .page{padding:24px 30px;max-width:680px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card);display:flex;flex-direction:column;gap:12px}
  label{display:flex;flex-direction:column;font-size:13px;color:var(--muted);gap:4px}label.row{flex-direction:row;align-items:center;gap:8px;color:var(--text)}
  input{padding:8px;border:1px solid var(--border);border-radius:8px;font:inherit;background:var(--card);color:var(--text)}
  .row{display:flex;gap:8px}button{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}button:disabled{opacity:.5}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px}.banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:var(--muted)}
</style>
