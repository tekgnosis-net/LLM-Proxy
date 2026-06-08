<script>
  let { store } = $props()

  const STRATEGIES = [
    'simple-shuffle',
    'least-busy',
    'usage-based-routing',
    'usage-based-routing-v2',
    'latency-based-routing',
    'cost-based-routing',
  ]

  // --- live reads from the item store ---
  let strategy     = $derived(store.itemNamed('router_setting', 'routing_strategy')?.data ?? 'simple-shuffle')
  let numRetries   = $derived(store.itemNamed('router_setting', 'num_retries')?.data ?? '')
  let timeout      = $derived(store.itemNamed('router_setting', 'timeout')?.data ?? '')
  let cooldown     = $derived(store.itemNamed('router_setting', 'cooldown_time')?.data ?? '')
  let allowedFails = $derived(store.itemNamed('router_setting', 'allowed_fails')?.data ?? '')
  let retryAfter   = $derived(store.itemNamed('router_setting', 'retry_after')?.data ?? '')
  let fallbacksRaw = $derived(store.itemNamed('router_setting', 'fallbacks')?.data ?? [])

  // local mutable copies (initialised from derived; user edits before staging)
  let localStrategy     = $state('simple-shuffle')
  let localNumRetries   = $state('')
  let localTimeout      = $state('')
  let localCooldown     = $state('')
  let localAllowedFails = $state('')
  let localRetryAfter   = $state('')
  let localFallbacks    = $state('[]')

  let parseErr = $state('')

  // keep local copies in sync whenever the store refreshes
  $effect(() => { localStrategy     = strategy })
  $effect(() => { localNumRetries   = numRetries   === '' ? '' : String(numRetries) })
  $effect(() => { localTimeout      = timeout      === '' ? '' : String(timeout) })
  $effect(() => { localCooldown     = cooldown     === '' ? '' : String(cooldown) })
  $effect(() => { localAllowedFails = allowedFails === '' ? '' : String(allowedFails) })
  $effect(() => { localRetryAfter   = retryAfter   === '' ? '' : String(retryAfter) })
  $effect(() => { localFallbacks    = JSON.stringify(fallbacksRaw, null, 2) })

  // --- flag helpers ---
  function flag(key) { return store.itemNamed('router_setting', key)?.flag }
  function isStaged(key) { const f = flag(key); return f === 'new' || f === 'changed' }

  // --- per-field save actions ---
  async function saveStrategy() {
    await store.stageItem('router_setting', 'routing_strategy', localStrategy)
  }

  async function saveNumeric(key, localVal) {
    if (localVal === '' || localVal == null) return
    await store.stageItem('router_setting', key, Number(localVal))
  }

  async function saveFallbacks() {
    parseErr = ''
    let parsed
    try { parsed = JSON.parse(localFallbacks) } catch { parseErr = 'Fallbacks must be valid JSON'; return }
    await store.stageItem('router_setting', 'fallbacks', parsed)
  }

  function resetField(key) {
    // re-derive by re-reading the item; the $effect above will sync local copies
    // but for immediate UX we can manually reset
    switch (key) {
      case 'routing_strategy': localStrategy     = strategy; break
      case 'num_retries':      localNumRetries   = numRetries   === '' ? '' : String(numRetries); break
      case 'timeout':          localTimeout      = timeout      === '' ? '' : String(timeout); break
      case 'cooldown_time':    localCooldown     = cooldown     === '' ? '' : String(cooldown); break
      case 'allowed_fails':    localAllowedFails = allowedFails === '' ? '' : String(allowedFails); break
      case 'retry_after':      localRetryAfter   = retryAfter   === '' ? '' : String(retryAfter); break
      case 'fallbacks':        localFallbacks    = JSON.stringify(fallbacksRaw, null, 2); parseErr = ''; break
    }
  }
</script>

<div class="page">
  <h1>Routing</h1>
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}

  <div class="card">

    <!-- Routing strategy -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Routing strategy {#if isStaged('routing_strategy')}<span class="staged-dot" title="staged — click Apply to make live">●</span>{/if}</span>
        <select bind:value={localStrategy}>
          {#each STRATEGIES as s}<option value={s}>{s}</option>{/each}
        </select>
      </label>
      <div class="field-actions">
        <button class="primary" onclick={saveStrategy} disabled={store.saving || store.applying}>Save</button>
        <button onclick={() => resetField('routing_strategy')} disabled={store.saving || store.applying}>Reset</button>
      </div>
    </div>
    <p class="hint">Cost-based picks the cheapest deployment in a model group. <code>lowest-cost</code> is not valid and is rejected.</p>

    <!-- Num retries -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Num retries {#if isStaged('num_retries')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localNumRetries} placeholder="default 3" />
      </label>
      <div class="field-actions">
        <button class="primary" onclick={() => saveNumeric('num_retries', localNumRetries)} disabled={store.saving || store.applying}>Save</button>
        <button onclick={() => resetField('num_retries')} disabled={store.saving || store.applying}>Reset</button>
      </div>
    </div>

    <!-- Timeout -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Timeout (s) {#if isStaged('timeout')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" step="0.1" bind:value={localTimeout} placeholder="default 600" />
      </label>
      <div class="field-actions">
        <button class="primary" onclick={() => saveNumeric('timeout', localTimeout)} disabled={store.saving || store.applying}>Save</button>
        <button onclick={() => resetField('timeout')} disabled={store.saving || store.applying}>Reset</button>
      </div>
    </div>

    <!-- Cooldown time -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Cooldown time (s) {#if isStaged('cooldown_time')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localCooldown} placeholder="after allowed_fails" />
      </label>
      <div class="field-actions">
        <button class="primary" onclick={() => saveNumeric('cooldown_time', localCooldown)} disabled={store.saving || store.applying}>Save</button>
        <button onclick={() => resetField('cooldown_time')} disabled={store.saving || store.applying}>Reset</button>
      </div>
    </div>

    <!-- Allowed fails -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Allowed fails {#if isStaged('allowed_fails')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localAllowedFails} placeholder="per minute before cooldown" />
      </label>
      <div class="field-actions">
        <button class="primary" onclick={() => saveNumeric('allowed_fails', localAllowedFails)} disabled={store.saving || store.applying}>Save</button>
        <button onclick={() => resetField('allowed_fails')} disabled={store.saving || store.applying}>Reset</button>
      </div>
    </div>

    <!-- Retry after -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Retry after (s) {#if isStaged('retry_after')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localRetryAfter} placeholder="min before retry" />
      </label>
      <div class="field-actions">
        <button class="primary" onclick={() => saveNumeric('retry_after', localRetryAfter)} disabled={store.saving || store.applying}>Save</button>
        <button onclick={() => resetField('retry_after')} disabled={store.saving || store.applying}>Reset</button>
      </div>
    </div>

    <!-- Fallbacks -->
    <div class="field-col">
      <span class="field-name">Fallbacks (JSON, e.g. <code>[{'{'}"gpt-4": ["gpt-4o"]{'}'}]</code>) {#if isStaged('fallbacks')}<span class="staged-dot" title="staged">●</span>{/if}</span>
      <textarea rows="5" bind:value={localFallbacks}></textarea>
      {#if parseErr}<div class="banner err">{parseErr}</div>{/if}
      <div class="row">
        <button class="primary" onclick={saveFallbacks} disabled={store.saving || store.applying}>Save</button>
        <button onclick={() => resetField('fallbacks')} disabled={store.saving || store.applying}>Reset</button>
      </div>
    </div>

  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:720px}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:var(--card,#fff);display:flex;flex-direction:column;gap:14px}
  /* field laid out as label + action buttons side by side */
  .field-row{display:flex;align-items:flex-end;gap:10px}
  .field-label{display:flex;flex-direction:column;font-size:13px;color:var(--text,#3a3a3c);gap:4px;flex:1}
  .field-name{display:flex;align-items:center;gap:5px;font-size:13px;color:var(--text,#3a3a3c)}
  .field-col{display:flex;flex-direction:column;font-size:13px;color:var(--text,#3a3a3c);gap:6px}
  .field-actions{display:flex;gap:6px;padding-bottom:0}
  select,input,textarea{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit;background:var(--card,#fff);color:var(--text,#1d1d1f)}
  textarea{font-family:ui-monospace,monospace}
  .row{display:flex;gap:8px}
  button{padding:6px 12px;border:1px solid #ccc;border-radius:8px;background:var(--card,#fff);font:inherit;cursor:pointer;white-space:nowrap;color:var(--text,#1d1d1f)}
  button.primary{background:#0a84ff;color:#fff;border:0}
  button:disabled{opacity:.5;cursor:not-allowed}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}
  .banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:var(--muted,#6e6e73);margin:0}
  /* staged indicator: small accent dot in the app's blue */
  .staged-dot{color:#0a84ff;font-size:10px;line-height:1;cursor:default;user-select:none}
</style>
