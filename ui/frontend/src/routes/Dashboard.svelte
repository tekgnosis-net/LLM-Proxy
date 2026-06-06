<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let health = $state(null); let err = $state('')
  onMount(async () => { try { health = await api.health() } catch (e) { err = e.message } })
  const ok = (b) => b ? '#34c759' : '#ff3b30'
</script>
<div style="padding:24px 30px;max-width:960px">
  <h1>Dashboard</h1>
  {#if err}<p style="color:#ff3b30">{err}</p>{/if}
  {#if health}
    <div style="display:flex;gap:14px;margin-top:12px">
      <div style="border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:14px 16px">
        <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{ok(health.proxy.reachable)}"></span>
        Proxy · {health.proxy.reachable ? 'reachable' : 'unreachable'}
      </div>
    </div>
    <pre style="margin-top:16px;background:#f5f5f7;padding:14px;border-radius:10px;font-size:12px;overflow:auto">{JSON.stringify(health, null, 2)}</pre>
  {/if}
</div>
