<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { PROVIDERS } from '../lib/providers.js'
  let { store } = $props()
  let creds = $state([]), err = $state(''), busy = $state(false), showAdd = $state(false)
  let form = $state({ credential_name:'', provider:'openai', api_key:'' })
  async function load(){ try{ creds = await api.credentials() }catch(e){ err=e.message } }
  onMount(load)
  async function add(){ busy=true; err=''
    try{ const r=await api.createCredential({credential_name:form.credential_name,provider:form.provider,api_key:form.api_key});
      if (store) { store.pending = true } ; form={credential_name:'',provider:'openai',api_key:''}; showAdd=false; await load(); store?.refreshPending?.() }
    catch(e){ err=e.message } finally{ busy=false } }
  async function del(n){ if(!confirm(`Delete "${n}"? Models using it will fail after the next Apply.`))return; busy=true
    try{ await api.deleteCredential(n); await load(); store?.refreshPending?.() }catch(e){ err=e.message } finally{ busy=false } }
</script>
<div class="page"><header><h1>Provider Keys</h1><button class="primary" onclick={()=>showAdd=!showAdd}>＋ Add key</button></header>
  {#if err}<div class="banner err">{err}</div>{/if}
  <p class="hint">Keys are encrypted at rest in the app database and written into <code>config.yaml</code> on Apply. Values are never shown again. Saving or deleting a key stages a change — click <strong>Apply</strong> to activate.</p>
  {#if showAdd}<div class="card add">
    <label>Name <input bind:value={form.credential_name} placeholder="e.g. openai_prod" /></label>
    <label>Provider <select bind:value={form.provider}>{#each PROVIDERS as p}<option value={p.id}>{p.label}</option>{/each}</select></label>
    <label>API key <input type="password" bind:value={form.api_key} placeholder="sk-…" /></label>
    <div class="row"><button class="primary" onclick={add} disabled={busy||!form.credential_name||!form.api_key}>Save key</button><button onclick={()=>showAdd=false}>Cancel</button></div>
  </div>{/if}
  <div class="card">{#if creds.length===0}<p class="empty">No provider keys yet.</p>{:else}
    <table><thead><tr><th>Name</th><th>Provider</th><th></th></tr></thead><tbody>
      {#each creds as k}<tr><td>{k.credential_name}</td><td>{k.provider||'—'}</td>
        <td><button class="danger" onclick={()=>del(k.credential_name)} disabled={busy}>Delete</button></td></tr>{/each}
    </tbody></table>{/if}</div>
</div>
<style>
  .page{padding:24px 30px;max-width:760px}header{display:flex;justify-content:space-between;align-items:center}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;margin-top:14px;background:var(--card,#fff)}
  .card.add{display:flex;flex-direction:column;gap:10px;max-width:420px}
  label{display:flex;flex-direction:column;font-size:13px;gap:4px;color:var(--muted,#3a3a3c)}
  input,select{padding:8px;border:1px solid var(--border,#ccc);border-radius:8px;font:inherit}
  table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--border,rgba(0,0,0,.06));font-size:14px}
  .row{display:flex;gap:8px}button{padding:8px 12px;border:1px solid var(--border,#ccc);border-radius:8px;background:var(--card,#fff);font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}button.danger{color:#ff3b30;border-color:#ffd0cc}button:disabled{opacity:.5}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;font-size:13px}.hint{font-size:12px;color:var(--muted,#6e6e73)}.empty{color:var(--muted,#6e6e73)}
</style>
