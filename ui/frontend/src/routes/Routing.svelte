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

  // --- single save / reset ---
  const FIELDS = ['routing_strategy','num_retries','timeout','cooldown_time','allowed_fails','retry_after']

  async function saveAll() {
    parseErr = ''
    // fallbacks: parse first; abort all on error
    let fb
    try { fb = JSON.parse(localFallbacks) } catch { parseErr = 'Fallbacks must be valid JSON'; return }
    const locals = { routing_strategy: localStrategy, num_retries: localNumRetries, timeout: localTimeout,
                     cooldown_time: localCooldown, allowed_fails: localAllowedFails, retry_after: localRetryAfter }
    const stored = { routing_strategy: strategy, num_retries: numRetries, timeout: timeout,
                     cooldown_time: cooldown, allowed_fails: allowedFails, retry_after: retryAfter }
    for (const k of FIELDS) {
      const v = locals[k]
      if (k === 'routing_strategy') { if (v !== stored[k]) await store.stageItem('router_setting', k, v); continue }
      if (v === '' || v == null) continue            // skip cleared numerics
      if (Number(v) !== Number(stored[k])) await store.stageItem('router_setting', k, Number(v))
    }
    if (JSON.stringify(fb) !== JSON.stringify(fallbacksRaw)) await store.stageItem('router_setting', 'fallbacks', fb)
  }

  function resetAll() { localStrategy = strategy; localNumRetries = numRetries===''?'':String(numRetries)
    localTimeout = timeout===''?'':String(timeout); localCooldown = cooldown===''?'':String(cooldown)
    localAllowedFails = allowedFails===''?'':String(allowedFails); localRetryAfter = retryAfter===''?'':String(retryAfter)
    localFallbacks = JSON.stringify(fallbacksRaw, null, 2); parseErr = '' }
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
    </div>
    <p class="hint">Cost-based picks the cheapest deployment in a model group. <code>lowest-cost</code> is not valid and is rejected.</p>

    <!-- Num retries -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Num retries {#if isStaged('num_retries')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localNumRetries} placeholder="default 3" />
      </label>
    </div>

    <!-- Timeout -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Timeout (s) {#if isStaged('timeout')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" step="0.1" bind:value={localTimeout} placeholder="default 600" />
      </label>
    </div>

    <!-- Cooldown time -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Cooldown time (s) {#if isStaged('cooldown_time')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localCooldown} placeholder="after allowed_fails" />
      </label>
    </div>

    <!-- Allowed fails -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Allowed fails {#if isStaged('allowed_fails')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localAllowedFails} placeholder="per minute before cooldown" />
      </label>
    </div>

    <!-- Retry after -->
    <div class="field-row">
      <label class="field-label">
        <span class="field-name">Retry after (s) {#if isStaged('retry_after')}<span class="staged-dot" title="staged">●</span>{/if}</span>
        <input type="number" min="0" bind:value={localRetryAfter} placeholder="min before retry" />
      </label>
    </div>

    <!-- Fallbacks -->
    <div class="field-col">
      <span class="field-name">Fallbacks (JSON, e.g. <code>[{'{'}"gpt-4": ["gpt-4o"]{'}'}]</code>) {#if isStaged('fallbacks')}<span class="staged-dot" title="staged">●</span>{/if}</span>
      <textarea rows="5" bind:value={localFallbacks}></textarea>
    </div>

    <!-- Footer -->
    {#if parseErr}<div class="banner err">{parseErr}</div>{/if}
    <div class="footer-row">
      <button class="primary" onclick={saveAll} disabled={store.saving||store.applying}>Save changes</button>
      <button onclick={resetAll} disabled={store.saving||store.applying}>Reset all</button>
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
  .footer-row{display:flex;gap:8px;padding-top:4px;border-top:1px solid rgba(0,0,0,.06)}
  select,input,textarea{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit;background:var(--card,#fff);color:var(--text,#1d1d1f)}
  textarea{font-family:ui-monospace,monospace}
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
