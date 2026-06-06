<script>
  import { onMount } from 'svelte'
  import { api } from './lib/api.js'
  import Login from './routes/Login.svelte'
  import Dashboard from './routes/Dashboard.svelte'
  import ConfigViewer from './routes/ConfigViewer.svelte'
  import Models from './routes/Models.svelte'
  import Routing from './routes/Routing.svelte'
  import { createConfigStore } from './lib/configStore.svelte.js'
  const store = createConfigStore()

  let authed = $state(false)
  let screen = $state('dash')
  onMount(async () => { authed = (await api.me()).authed })
  async function onLogin() { authed = true }
  async function logout() { await api.logout(); authed = false }
</script>

{#if !authed}
  <Login {onLogin} />
{:else}
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><span class="logo">LP</span> LLM Proxy</div>
      <div class="navgroup">Overview</div>
      <button class="nav" class:active={screen==='dash'} onclick={() => screen='dash'}>▦ Dashboard</button>
      <div class="navgroup">Configuration</div>
      <button class="nav" class:active={screen==='models'} onclick={() => screen='models'}>◳ Models</button>
      <button class="nav" class:active={screen==='routing'} onclick={() => screen='routing'}>⇄ Routing</button>
      <button class="nav" class:active={screen==='config'} onclick={() => screen='config'}>◈ config.yaml</button>
      <div class="spacer"></div>
      <button class="nav" onclick={logout}>⎋ Sign out</button>
    </aside>
    <main class="main">
      {#if screen==='dash'}<Dashboard />
      {:else if screen==='models'}<Models {store} />
      {:else if screen==='routing'}<Routing {store} />
      {:else}<ConfigViewer />{/if}
    </main>
  </div>
{/if}

<style>
  :global(body){margin:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;color:#1d1d1f;-webkit-font-smoothing:antialiased}
  .app{display:grid;grid-template-columns:236px 1fr;height:100vh}
  .sidebar{background:#f5f5f7;border-right:1px solid rgba(0,0,0,.08);padding:18px 12px;display:flex;flex-direction:column}
  .brand{display:flex;align-items:center;gap:9px;padding:2px 8px 16px;font-weight:600}
  .logo{width:24px;height:24px;border-radius:7px;background:linear-gradient(135deg,#0a84ff,#5e5ce6);color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700}
  .navgroup{margin:12px 6px 4px;font-size:11px;text-transform:uppercase;color:#6e6e73;font-weight:600}
  .nav{display:block;width:100%;text-align:left;border:0;background:none;padding:7px 10px;border-radius:8px;font:inherit;cursor:pointer}
  .nav.active{background:#0a84ff;color:#fff}
  .spacer{flex:1}
  .main{overflow:auto}
</style>
