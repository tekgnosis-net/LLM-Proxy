<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let { store } = $props()
  let rendered = $state(null); let err = $state('')
  onMount(async () => {
    try { const r = await api.configRendered(); rendered = r.config } catch (e) { err = e.message }
  })
</script>
<div style="padding:24px 30px;max-width:960px">
  <h1>config.yaml <span style="font-size:13px;color:var(--muted)">(rendered preview — read-only)</span></h1>
  <p style="font-size:12px;color:var(--muted);margin:4px 0 12px">This is what will be written to config.yaml on Apply (secrets redacted).</p>
  {#if err}<p style="color:#ff3b30">{err}</p>{/if}
  {#if rendered != null}
    <pre style="background:var(--card);border:1px solid var(--border);padding:14px;border-radius:10px;font-size:12px;overflow:auto;color:var(--text)">{JSON.stringify(rendered, null, 2)}</pre>
  {:else if !err}
    <p style="color:var(--muted)">Loading…</p>
  {/if}
</div>
