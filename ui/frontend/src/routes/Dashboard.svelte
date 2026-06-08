<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let { store } = $props()
  let health = $state(null), usage = $state(null), keys = $state(null), err = $state('')
  onMount(async () => {
    try {
      await store.load()
      health = await api.health()
      usage = await api.usage().catch(() => null)
      keys = await api.keys().catch(() => null)
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
</div>
<style>
  .page{padding:24px 30px;max-width:1000px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-top:16px}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;background:var(--card,#fff)}
  .lbl{font-size:12px;color:var(--muted,#6e6e73)}.big{font-size:26px;font-weight:600;margin-top:6px;display:flex;align-items:center;gap:8px}
  .sub{font-size:12px;color:var(--muted,#6e6e73);margin-top:4px}
  .d{width:10px;height:10px;border-radius:50%;display:inline-block}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
</style>
