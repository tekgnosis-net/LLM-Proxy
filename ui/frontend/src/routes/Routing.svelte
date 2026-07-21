<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'

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

  // --- integrity panel ---
  let integ = $state(null)
  let integBusy = $state(false)
  let integErr = $state('')
  async function loadIntegrity() {
    try { integ = await api.integrity(); integErr = integ?.error ? 'Integrity check failed (proxy/key API).' : '' }
    catch (e) { integErr = e.message }
  }
  onMount(loadIntegrity)
  async function fixOrphan(o) {
    integBusy = true
    try {
      const prev = await api.integrityFix(o, true)               // dry-run preview
      const msg = `Remove ${o.reference} from ${o.location}?\n\n` +
                  `${o.scope === 'router' ? 'Stages a change — needs Apply (restart).' : 'Applies immediately (hot).'}`
      if (!confirm(msg)) return
      await api.integrityFix(o, false)
      await loadIntegrity()
      if (o.scope === 'router') await store.load()               // reflect the newly-staged change
    } catch (e) { integErr = e.message }
    finally { integBusy = false }
  }
  let orphanCount = $derived((integ?.router_orphans?.length || 0) + (integ?.key_orphans?.length || 0))

  // --- reachability panel ---
  let reach = $state(null)
  let reachErr = $state('')
  async function loadReach() {
    try { reach = await api.reachability(); reachErr = reach?.error ? 'Reachability check failed (key API).' : '' }
    catch (e) { reachErr = e.message }
  }
  onMount(loadReach)
  let reachCount = $derived((reach?.collisions?.length || 0) + (reach?.key_over_reach?.length || 0))

  // --- per-group routing ---

  // available model names (deduped) from the model items
  let availableModels = $derived(
    [...new Set(store.itemsOfKind('model').map(m => m.data?.model_name).filter(Boolean))]
  )

  // deep-copy helper
  function deepCopy(v) { return JSON.parse(JSON.stringify(v)) }

  // live read of stored groups
  let storedGroups = $derived(store.itemNamed('router_setting', 'routing_groups')?.data ?? [])

  // local editable copy of groups
  let localGroups = $state([])

  // keep local groups in sync when store refreshes
  $effect(() => { localGroups = deepCopy(storedGroups) })

  // collapsible toggle
  let groupsOpen = $state(false)

  // overlap warning for the groups editor
  let groupsOverlapWarn = $state('')

  function isGroupsStaged() {
    const f = store.itemNamed('router_setting', 'routing_groups')?.flag
    return f === 'new' || f === 'changed' || f === 'deleted'
  }

  function addGroup() {
    localGroups = [...localGroups, { group_name: '', models: [], routing_strategy: 'simple-shuffle' }]
  }

  function removeGroup(idx) {
    localGroups = localGroups.filter((_, i) => i !== idx)
  }

  function toggleModel(groupIdx, modelName) {
    const g = localGroups[groupIdx]
    const models = g.models || []
    if (models.includes(modelName)) {
      localGroups[groupIdx] = { ...g, models: models.filter(m => m !== modelName) }
    } else {
      localGroups[groupIdx] = { ...g, models: [...models, modelName] }
    }
  }

  function checkOverlap(groups) {
    const seen = new Set()
    for (const g of groups) {
      for (const m of g.models || []) {
        if (seen.has(m)) return `Model "${m}" is in more than one routing group — each model may belong to at most one group.`
        seen.add(m)
      }
    }
    return ''
  }

  async function saveGroups() {
    groupsOverlapWarn = ''
    // drop empty groups (no name or no models)
    const cleaned = localGroups.filter(g => g.group_name && g.models && g.models.length > 0)
    // client-side overlap check
    const warn = checkOverlap(cleaned)
    if (warn) { groupsOverlapWarn = warn; return }
    if (cleaned.length === 0) {
      await store.deleteItem('router_setting', 'routing_groups')
    } else {
      await store.stageItem('router_setting', 'routing_groups', cleaned)
    }
  }
</script>

<div class="page">
  <h1>Routing</h1>
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}

  <section class="card">
    <h2>Referential integrity
      {#if orphanCount > 0}<span class="badge-warn">⚠ {orphanCount} dangling</span>{/if}</h2>
    {#if integErr}<div class="banner err">{integErr}</div>
    {:else if !integ}<p class="hint">Checking…</p>
    {:else if orphanCount === 0}<p class="hint">✓ No dangling references.</p>
    {:else}
      <p class="hint">These config references name a model group that doesn't exist. Removing them prevents unintended fallback routing.</p>
      <ul class="orphans">
        {#each [...(integ.router_orphans || []), ...(integ.key_orphans || [])] as o (`${o.scope}-${o.location}-${o.reference}-${JSON.stringify(o.target)}`)}
          <li>
            <span class="mono">{o.location}</span> → missing <span class="mono red">{o.reference}</span>
            <button onclick={() => fixOrphan(o)} disabled={integBusy || store.applying || store.saving}>Fix</button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="card">
    <h2>Reachability (advisory)
      {#if reachCount > 0}<span class="badge-info">{reachCount}</span>{/if}
      {#if reach?.semantics_version}<span class="caption">semantics: LiteLLM {reach.semantics_version}</span>{/if}</h2>
    {#if reachErr}<div class="banner err">{reachErr}</div>
    {:else if !reach}<p class="hint">Checking…</p>
    {:else if reachCount === 0}<p class="hint">✓ No cross-group fallback paths.</p>
    {:else}
      <p class="hint">Fallbacks can route a failed request to a group the caller wasn't granted (LiteLLM applies fallbacks without re-checking per-key access). This is informational — to remove a path, grant the key the target group or re-scope the fallback.</p>
      <ul class="orphans">
        {#each reach.key_over_reach as k (k.token)}
          {#each k.extra as e (e.target + e.via_group)}
            <li><span class="mono">key {k.key_alias}</span> can also reach <span class="mono amber">{e.target}</span> (via <span class="mono">{e.via_group}</span> → fallback <span class="mono">{e.via_fallback}</span>)</li>
          {/each}
        {/each}
        {#each reach.collisions as c (c.group + '|' + c.fallback_key + '|' + (c.deployment_id||'') + '|' + c.fallback_setting)}
          <li><span class="mono">{c.group}</span>{#if c.base_model} (deployment <span class="mono">{c.base_model}</span>){/if} → can route to <span class="mono amber">{c.targets.join(', ')}</span> via <span class="mono">{c.fallback_setting}</span> → <span class="mono">{c.fallback_key}</span></li>
        {/each}
      </ul>
    {/if}
  </section>

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

  <!-- Per-group routing (advanced) -->
  <div class="card">
    <button class="collapsible-header" onclick={() => { groupsOpen = !groupsOpen }}>
      <span class="field-name">
        Per-group routing (advanced)
        {#if isGroupsStaged()}<span class="staged-dot" title="staged — click Apply to make live">●</span>{/if}
      </span>
      <span class="chevron">{groupsOpen ? '▲' : '▼'}</span>
    </button>
    {#if groupsOpen}
      <p class="hint">Assign a per-group routing strategy. Each model name may belong to at most one group. Groups render into <code>router_settings.routing_groups</code>.</p>

      {#if groupsOverlapWarn}<div class="banner err">{groupsOverlapWarn}</div>{/if}

      {#each localGroups as group, idx}
        <div class="group-row">
          <div class="group-fields">
            <label class="field-label">
              <span class="field-name">Group name</span>
              <input type="text" bind:value={group.group_name} placeholder="e.g. fast-models" />
            </label>
            <label class="field-label">
              <span class="field-name">Strategy</span>
              <select bind:value={group.routing_strategy}>
                {#each STRATEGIES as s}<option value={s}>{s}</option>{/each}
              </select>
            </label>
            <div class="field-label">
              <span class="field-name">Models</span>
              {#if availableModels.length === 0}
                <span class="hint">No models yet — add models first.</span>
              {:else}
                <div class="model-checkboxes">
                  {#each availableModels as modelName}
                    <label class="checkbox-label">
                      <input
                        type="checkbox"
                        checked={(group.models || []).includes(modelName)}
                        onchange={() => toggleModel(idx, modelName)}
                      />
                      {modelName}
                    </label>
                  {/each}
                </div>
              {/if}
            </div>
          </div>
          <button class="remove-btn" onclick={() => removeGroup(idx)} title="Remove group">✕</button>
        </div>
        {#if idx < localGroups.length - 1}<hr class="group-sep" />{/if}
      {/each}

      <div class="group-footer">
        <button onclick={addGroup} disabled={store.saving||store.applying}>Add group</button>
        <button class="primary" onclick={saveGroups} disabled={store.saving||store.applying}>Save groups</button>
      </div>
    {/if}
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
  select,input[type="text"],input[type="number"],textarea{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit;background:var(--card,#fff);color:var(--text,#1d1d1f)}
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
  /* per-group section */
  .collapsible-header{display:flex;align-items:center;justify-content:space-between;width:100%;background:none;border:0;padding:0;cursor:pointer;font:inherit}
  .chevron{font-size:11px;color:var(--muted,#6e6e73)}
  .group-row{display:flex;align-items:flex-start;gap:10px}
  .group-fields{display:flex;flex-direction:column;gap:10px;flex:1}
  .group-sep{border:0;border-top:1px solid rgba(0,0,0,.06);margin:4px 0}
  .group-footer{display:flex;gap:8px;padding-top:4px;border-top:1px solid rgba(0,0,0,.06)}
  .remove-btn{padding:4px 8px;border:1px solid #ccc;border-radius:8px;background:none;cursor:pointer;color:var(--muted,#6e6e73);font-size:12px;align-self:flex-start;margin-top:18px}
  .model-checkboxes{display:flex;flex-wrap:wrap;gap:8px;padding:6px 0}
  .checkbox-label{display:flex;align-items:center;gap:4px;font-size:13px;color:var(--text,#3a3a3c);cursor:pointer}
  .badge-warn{background:#fff4e5;color:#9a5b00;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:600;margin-left:8px}
  .badge-info{background:#e5effb;color:#0a4a8f;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:600;margin-left:8px}
  .caption{font-size:11px;color:#6e6e73;font-weight:400;margin-left:8px}
  .amber{color:#9a5b00}
  .orphans{list-style:none;padding:0;margin:8px 0}
  .orphans li{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid rgba(0,0,0,.06);font-size:13px}
  .orphans button{margin-left:auto;font-size:12px;padding:3px 12px;border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer}
  .red{color:#c0271d}
  .mono{font-family:ui-monospace,monospace}
</style>
