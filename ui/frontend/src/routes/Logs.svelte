<script>
  import { onMount, onDestroy, tick } from 'svelte'
  let { store } = $props()

  // --- log stream state ---
  let lines = $state([])
  let paused = $state(false)
  let preEl = $state(null)
  let es = null

  function openStream() {
    if (es) { es.close(); es = null }
    es = new EventSource('/api/logs/stream?tail=200')
    es.onmessage = async (e) => {
      lines = lines.length >= 2000
        ? [...lines.slice(lines.length - 1999), e.data]
        : [...lines, e.data]
      await tick()
      if (preEl) preEl.scrollTop = preEl.scrollHeight
    }
    es.onerror = () => {
      // EventSource auto-reconnects; nothing needed here
    }
  }

  function togglePause() {
    if (paused) {
      paused = false
      openStream()
    } else {
      paused = true
      if (es) { es.close(); es = null }
    }
  }

  function clearLogs() { lines = [] }

  onMount(() => openStream())
  onDestroy(() => { if (es) { es.close(); es = null } })

  // --- Debug logging toggle ---
  const verboseItem = $derived(store.itemNamed('litellm_setting', 'set_verbose'))
  const verboseOn = $derived(verboseItem?.data === true || verboseItem?.data === 'true')
  let applying = $state(false)

  async function onDebugToggle(e) {
    const checked = e.target.checked
    const msg = checked
      ? 'Raising the log level restarts LiteLLM (~20s) and drops in-flight requests. Continue?'
      : 'Lowering the log level restarts LiteLLM (~20s). Continue?'
    if (!confirm(msg)) {
      // revert the checkbox visually — it will re-derive from store
      e.target.checked = !checked
      return
    }
    applying = true
    try {
      await store.stageItem('litellm_setting', 'set_verbose', checked)
      await store.apply()
    } finally {
      applying = false
    }
  }
</script>

<div class="page">
  <div class="header-row">
    <div>
      <h1>Logs</h1>
      <p class="note">Admin-only. Debug level can include request content.</p>
    </div>
    <div class="controls">
      <label class="toggle-label" class:dim={applying}>
        <span>Debug logging</span>
        <input
          type="checkbox"
          checked={verboseOn}
          disabled={applying || store.applying}
          onchange={onDebugToggle}
        />
        <span class="toggle-status">{verboseOn ? 'On' : 'Off'}</span>
        {#if applying || store.applying}<span class="applying-hint">applying…</span>{/if}
      </label>
      <button class="btn" onclick={togglePause}>{paused ? 'Resume' : 'Pause'}</button>
      <button class="btn" onclick={clearLogs}>Clear</button>
    </div>
  </div>

  {#if paused}
    <div class="paused-banner">Stream paused — <button class="link-btn" onclick={togglePause}>Resume</button></div>
  {/if}

  <pre class="log-box" bind:this={preEl}>{#each lines as line}{line + '\n'}{/each}{#if lines.length === 0}<span class="empty">Waiting for log lines…</span>{/if}</pre>
</div>

<style>
  .page { padding: 24px 30px; max-width: 1000px; display: flex; flex-direction: column; gap: 0 }
  h1 { margin: 0 0 2px }
  .note { font-size: 12px; color: var(--muted, #6e6e73); margin: 0 }
  .header-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; flex-wrap: wrap }
  .controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap }
  .btn { font-size: 13px; padding: 5px 14px; border: 1px solid var(--border, rgba(0,0,0,.12)); border-radius: 8px; background: var(--card, #fff); cursor: pointer; color: inherit; font-family: inherit }
  .btn:hover { background: var(--border, rgba(0,0,0,.06)) }
  .toggle-label { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; user-select: none }
  .toggle-label.dim { opacity: 0.6 }
  .toggle-status { font-size: 12px; color: var(--muted, #6e6e73); min-width: 22px }
  .applying-hint { font-size: 11px; color: var(--muted, #6e6e73) }
  .paused-banner { background: #fff7e6; border: 1px solid #ffe1a8; border-radius: 8px; padding: 6px 14px; font-size: 13px; color: #7a5b00; margin-bottom: 10px }
  .link-btn { background: none; border: none; cursor: pointer; color: #7a5b00; text-decoration: underline; font: inherit; padding: 0 }
  .log-box { background: var(--card, #1a1a1a); color: #d4d4d4; border: 1px solid var(--border, rgba(0,0,0,.12)); border-radius: 10px; padding: 14px 16px; font-family: "SF Mono", "Fira Code", monospace; font-size: 12px; line-height: 1.5; overflow-y: auto; height: calc(100vh - 220px); min-height: 300px; white-space: pre-wrap; word-break: break-all; margin: 0 }
  :global([data-theme="dark"]) .log-box { background: #0d0d0d; color: #d4d4d4 }
  :global([data-theme="light"]) .log-box { background: #1a1a1a; color: #d4d4d4 }
  .empty { color: #666; font-style: italic }
</style>
