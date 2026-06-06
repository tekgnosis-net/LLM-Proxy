<script>
  import { onMount } from 'svelte'
  let { store } = $props()
  const STRATEGIES = ['simple-shuffle','least-busy','usage-based-routing','usage-based-routing-v2','latency-based-routing','cost-based-routing']
  let strategy = $state('simple-shuffle')
  let numRetries = $state('')
  let fallbacksText = $state('[]')
  let parseErr = $state('')
  onMount(async () => { if (!store.config) await store.load(); sync() })
  function sync() {
    const rs = store.config?.router_settings ?? {}
    strategy = rs.routing_strategy ?? 'simple-shuffle'
    numRetries = rs.num_retries ?? ''
    fallbacksText = JSON.stringify(rs.fallbacks ?? [], null, 2)
  }
  async function save() {
    parseErr = ''
    let fallbacks
    try { fallbacks = JSON.parse(fallbacksText) } catch (e) { parseErr = 'Fallbacks must be valid JSON'; return }
    const rs = { ...(store.config?.router_settings ?? {}), routing_strategy: strategy, fallbacks }
    if (numRetries !== '' && numRetries != null) rs.num_retries = Number(numRetries); else delete rs.num_retries
    await store.saveSection('router_settings', rs)
  }
</script>

<div class="page">
  <h1>Routing</h1>
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}
  <div class="card">
    <label>Routing strategy
      <select bind:value={strategy}>{#each STRATEGIES as s}<option value={s}>{s}</option>{/each}</select>
    </label>
    <p class="hint">Cost-based picks the cheapest deployment in a model group. <code>lowest-cost</code> is not valid and is rejected.</p>
    <label>Num retries <input type="number" min="0" bind:value={numRetries} placeholder="default 3" /></label>
    <label>Fallbacks (JSON, e.g. <code>[{'{'}"gpt-4": ["gpt-4o"]{'}'}]</code>)
      <textarea rows="5" bind:value={fallbacksText}></textarea>
    </label>
    {#if parseErr}<div class="banner err">{parseErr}</div>{/if}
    <div class="row">
      <button class="primary" onclick={save} disabled={store.applying}>Save &amp; apply</button>
      <button onclick={sync} disabled={store.applying}>Reset</button>
    </div>
  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:720px}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff;display:flex;flex-direction:column;gap:12px}
  label{display:flex;flex-direction:column;font-size:13px;color:#3a3a3c;gap:4px}
  select,input,textarea{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit}
  textarea{font-family:ui-monospace,monospace}
  .row{display:flex;gap:8px}
  button{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}button:disabled{opacity:.5}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:#6e6e73}
</style>
