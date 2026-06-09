<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { copyText } from '../lib/browser.js'
  let { store } = $props()
  let health = $state(null), usage = $state(null), keys = $state(null), err = $state('')
  let proxy = $state(null)
  onMount(async () => {
    try {
      await store.load()
      health = await api.health()
      usage = await api.usage().catch(() => null)
      keys = await api.keys().catch(() => null)
      proxy = await api.proxyInfo().catch(() => null)
    } catch (e) { err = e.message }
  })
  const dot = (ok) => ok ? '#34c759' : '#ff3b30'
  function modelCount() { return store.itemsOfKind('model').length }
  function keyCount() { return Array.isArray(keys) ? keys.length : '—' }
  function spend() { return usage?.total?.spend != null ? `$${Number(usage.total.spend).toFixed(2)}` : '$0.00' }
  function cacheOn() { return store.itemNamed('litellm_setting', 'cache')?.data ? 'on' : 'off' }
  function cacheType() {
    const cp = store.itemNamed('litellm_setting', 'cache_params')
    return cp?.data?.type ?? '—'
  }
  function baseUrl() {
    if (!proxy) return ''
    const host = proxy.proxy_host || location.hostname
    return `${location.protocol}//${host}:${proxy.proxy_port}`
  }
  async function copy(text) {
    await copyText(text)
  }
</script>
<div class="page">
  <h1>Dashboard</h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  <div class="cards">
    <div class="card"><div class="lbl">Proxy</div>
      <div class="big"><span class="d" style="background:{dot(health?.proxy?.reachable)}"></span>{health?.proxy?.reachable ? 'Healthy' : 'Down'}</div>
      <div class="sub">{health?.proxy?.raw?.db === 'connected' ? 'DB connected' : '—'}</div></div>
    <div class="card"><div class="lbl">Models</div><div class="big">{modelCount()}</div><div class="sub">in config</div></div>
    <div class="card"><div class="lbl">Virtual keys</div><div class="big">{keyCount()}</div><div class="sub">active</div></div>
    <div class="card"><div class="lbl">Spend (30d)</div><div class="big">{spend()}</div><div class="sub">all keys</div></div>
    <div class="card"><div class="lbl">Cache</div><div class="big">{cacheOn()}</div><div class="sub">{cacheType()}</div></div>
  </div>

  {#if proxy}
  <div class="card url-card">
    <div class="lbl">Proxy endpoint</div>
    <div class="url-row">
      <span class="url-label">Base URL</span>
      <code class="url-val">{baseUrl()}</code>
      <button class="copy-btn" onclick={() => copy(baseUrl())} title="Copy">Copy</button>
    </div>
    <div class="url-row">
      <span class="url-label">OpenAI SDK <code>base_url</code></span>
      <code class="url-val">{baseUrl()}/v1</code>
      <button class="copy-btn" onclick={() => copy(baseUrl() + '/v1')} title="Copy">Copy</button>
    </div>
    <p class="hint">Point OpenAI-compatible clients at the <code>/v1</code> URL with a virtual key.</p>
  </div>
  {/if}
</div>
<style>
  .page{padding:24px 30px;max-width:1000px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-top:16px}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;background:var(--card,#fff)}
  .lbl{font-size:12px;color:var(--muted,#6e6e73)}.big{font-size:26px;font-weight:600;margin-top:6px;display:flex;align-items:center;gap:8px}
  .sub{font-size:12px;color:var(--muted,#6e6e73);margin-top:4px}
  .d{width:10px;height:10px;border-radius:50%;display:inline-block}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
  .url-card{margin-top:16px}
  .url-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border,rgba(0,0,0,.06));flex-wrap:wrap}
  .url-row:last-of-type{border-bottom:none}
  .url-label{font-size:13px;color:var(--muted,#6e6e73);min-width:160px;flex-shrink:0}
  .url-val{font-size:13px;flex:1;word-break:break-all}
  .copy-btn{font-size:12px;padding:3px 10px;border:1px solid var(--border,rgba(0,0,0,.12));border-radius:6px;background:var(--card,#fff);cursor:pointer;color:inherit;flex-shrink:0}
  .copy-btn:hover{background:var(--border,rgba(0,0,0,.06))}
  .hint{font-size:12px;color:var(--muted,#6e6e73);margin-top:10px}
</style>
