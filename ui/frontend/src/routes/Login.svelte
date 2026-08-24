<script>
  import { api } from '../lib/api.js'
  let { onLogin } = $props()
  let password = $state(''); let error = $state('')
  async function submit(e) {
    e.preventDefault()
    try { await api.login(password); onLogin() } catch (err) { error = err.message }
  }
</script>
<div style="display:flex;align-items:center;justify-content:center;height:100vh;background:var(--sidebar)">
  <form onsubmit={submit} style="background:var(--card);padding:32px;border-radius:14px;box-shadow:0 10px 30px rgba(0,0,0,.1);width:320px;border:1px solid var(--border)">
    <h2 style="margin:0 0 16px;color:var(--text)">LLM Proxy</h2>
    <input type="password" bind:value={password} placeholder="Admin password"
      style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;margin-bottom:12px;background:var(--bg);color:var(--text)" />
    {#if error}<p style="color:#ff3b30;font-size:13px">{error}</p>{/if}
    <button style="width:100%;padding:10px;background:#0a84ff;color:#fff;border:0;border-radius:8px;font-weight:600">Sign in</button>
  </form>
</div>
