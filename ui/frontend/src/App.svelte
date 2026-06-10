<script>
  import { onMount } from 'svelte'
  import { api } from './lib/api.js'
  import Login from './routes/Login.svelte'
  import Dashboard from './routes/Dashboard.svelte'
  import ConfigViewer from './routes/ConfigViewer.svelte'
  import Models from './routes/Models.svelte'
  import Routing from './routes/Routing.svelte'
  import Keys from './routes/Keys.svelte'
  import ProviderKeys from './routes/ProviderKeys.svelte'
  import Usage from './routes/Usage.svelte'
  import Caching from './routes/Caching.svelte'
  import Housekeeping from './routes/Housekeeping.svelte'
  import Settings from './routes/Settings.svelte'
  import Logs from './routes/Logs.svelte'
  import { createConfigStore } from './lib/configStore.svelte.js'
  const store = createConfigStore()

  let authed = $state(false)
  let screen = $state('dash')
  let theme = $state(localStorage.getItem('theme') || 'light')

  $effect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  })

  function setTheme(t) { theme = t }

  onMount(async () => { authed = (await api.me()).authed; store.load() })
  async function onLogin() { authed = true; store.load() }
  async function logout() { await api.logout(); authed = false }
  function confirmDiscard() {
    if (confirm(`Discard all ${store.count} unapplied change(s)? Reverts to the last applied config.`)) {
      store.discard()
    }
  }
</script>

{#if !authed}
  <Login {onLogin} />
{:else}
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><span class="logo">LP</span> LLM Proxy</div>
      <div class="navgroup">Overview</div>
      <button class="nav" class:active={screen==='dash'} onclick={() => screen='dash'}>▦ Dashboard</button>
      <button class="nav" class:active={screen==='usage'} onclick={() => screen='usage'}>📊 Usage &amp; Spend</button>
      <div class="navgroup">Configuration</div>
      <button class="nav" class:active={screen==='models'} onclick={() => screen='models'}>◳ Models</button>
      <button class="nav" class:active={screen==='routing'} onclick={() => screen='routing'}>⇄ Routing</button>
      <button class="nav" class:active={screen==='caching'} onclick={() => screen='caching'}>⚡ Caching</button>
      <button class="nav" class:active={screen==='config'} onclick={() => screen='config'}>◈ config.yaml</button>
      <div class="navgroup">Access</div>
      <button class="nav" class:active={screen==='keys'} onclick={() => screen='keys'}>🔑 Virtual Keys</button>
      <button class="nav" class:active={screen==='providerkeys'} onclick={() => screen='providerkeys'}>🗝 Provider Keys</button>
      <div class="navgroup">System</div>
      <button class="nav" class:active={screen==='logs'} onclick={() => screen='logs'}>📋 Logs</button>
      <button class="nav" class:active={screen==='housekeeping'} onclick={() => screen='housekeeping'}>🧹 Housekeeping</button>
      <button class="nav" class:active={screen==='settings'} onclick={() => screen='settings'}>⚙ Settings</button>
      <div class="spacer"></div>
      <button class="nav" onclick={logout}>⎋ Sign out</button>
    </aside>
    <main class="main">
      {#if store.pending}
        <div class="applybar">
          <span><strong>{store.count}</strong> unapplied change{store.count === 1 ? '' : 's'}</span>
          <div class="applybar-actions">
            <button class="discard" onclick={confirmDiscard} disabled={store.saving || store.applying}>Discard all</button>
            <button class="apply" onclick={() => store.apply()} disabled={store.applying || store.saving}>{store.applying ? 'Applying… (~25s)' : 'Apply'}</button>
          </div>
        </div>
      {/if}
      {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
      {#if store.error}<div class="banner err">{store.error}</div>{/if}
      {#if screen==='dash'}<Dashboard {store} />
      {:else if screen==='models'}<Models {store} />
      {:else if screen==='routing'}<Routing {store} />
      {:else if screen==='caching'}<Caching {store} />
      {:else if screen==='keys'}<Keys />
      {:else if screen==='providerkeys'}<ProviderKeys {store} />
      {:else if screen==='usage'}<Usage />
      {:else if screen==='logs'}<Logs {store} />
      {:else if screen==='housekeeping'}<Housekeeping />
      {:else if screen==='settings'}<Settings {store} {theme} {setTheme} />
      {:else}<ConfigViewer {store} />{/if}
    </main>
  </div>
{/if}

<style>
  :global(:root){--bg:#fff;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--border:rgba(0,0,0,.08);--sidebar:#f5f5f7}
  :global([data-theme="dark"]){--bg:#1c1c1e;--card:#2c2c2e;--text:#f5f5f7;--muted:#98989d;--border:rgba(255,255,255,.12);--sidebar:#161618}
  :global(body){margin:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;-webkit-font-smoothing:antialiased;background:var(--bg);color:var(--text)}
  .app{display:grid;grid-template-columns:236px 1fr;height:100vh}
  .sidebar{background:var(--sidebar);border-right:1px solid var(--border);padding:18px 12px;display:flex;flex-direction:column}
  .brand{display:flex;align-items:center;gap:9px;padding:2px 8px 16px;font-weight:600;color:var(--text)}
  .logo{width:24px;height:24px;border-radius:7px;background:linear-gradient(135deg,#0a84ff,#5e5ce6);color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700}
  .navgroup{margin:12px 6px 4px;font-size:11px;text-transform:uppercase;color:var(--muted);font-weight:600}
  .nav{display:block;width:100%;text-align:left;border:0;background:none;padding:7px 10px;border-radius:8px;font:inherit;cursor:pointer;color:var(--text)}
  .nav.active{background:#0a84ff;color:#fff}
  .spacer{flex:1}
  .main{overflow:auto;background:var(--bg)}
  .applybar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;justify-content:space-between;
    background:#fff7e6;border-bottom:1px solid #ffe1a8;padding:8px 16px;font-size:13px;color:#7a5b00}
  .applybar-actions{display:flex;gap:8px;align-items:center}
  .applybar .apply{background:#ff9f0a;color:#fff;border:0;border-radius:8px;padding:6px 14px;font-weight:600;cursor:pointer}
  .applybar .apply:disabled{opacity:.6}
  .applybar .discard{background:transparent;color:#7a5b00;border:1px solid #e0c074;border-radius:8px;padding:6px 12px;font-weight:600;cursor:pointer}
  .applybar .discard:disabled{opacity:.5}
  .banner{padding:8px 16px;font-size:13px;font-weight:500}
  .banner.ok{background:#e8f9ee;color:#1a7f45;border-bottom:1px solid #a8e6bf}
  .banner.err{background:#fff0f0;color:#b00020;border-bottom:1px solid #f5b8c4}
</style>
