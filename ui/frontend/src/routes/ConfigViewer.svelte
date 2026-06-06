<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let cfg = $state(null); let err = $state('')
  onMount(async () => { try { cfg = await api.config() } catch (e) { err = e.message } })
</script>
<div style="padding:24px 30px;max-width:960px">
  <h1>config.yaml <span style="font-size:13px;color:#6e6e73">(read-only — editing lands in Phase 2)</span></h1>
  {#if err}<p style="color:#ff3b30">{err}</p>{/if}
  {#if cfg}<pre style="background:#f5f5f7;padding:14px;border-radius:10px;font-size:12px;overflow:auto">{JSON.stringify(cfg, null, 2)}</pre>{/if}
</div>
