<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uuidv4 } from '../lib/browser.js'
  import { money, fmtDateTime } from '../lib/format.js'
  import { headerRowsToDict, dictToHeaderRows, costRowsToDict, dictToCostRows,
           listRowsToArray, arrayToListRows, buildMcpInfo, validateMcpForm } from '../lib/mcp.js'
  let { store } = $props()

  let showAdd = $state(false)
  let editingId = $state(null)
  let formErr = $state('')
  let form = $state({ server_name: '', description: '', transport: 'http', url: '',
                      auth_type: '', auth_value: '', allow_all_keys: false,
                      default_cost: '', hasStoredSecret: false })
  let headerRows = $state([])   // static headers [{k,v}]
  let extraRows = $state([])    // forwarded header names, string[]
  let toolRows = $state([])     // allowed tools, string[]
  let costRows = $state([])     // per-tool costs [{tool,cost}]

  let mcpItems = $derived(store.itemsOfKind('mcp_server'))

  let healthMap = $state({})    // server_id → {status,last_health_check,health_check_error}
  let probing = $state({})      // server_id → true while a live Test runs
  let probeRes = $state({})     // server_id → {ok,msg}
  let usage = $state([])
  let usageErr = $state('')
  let toolsOpen = $state(null)  // server_id with the tools browser expanded
  let toolsState = $state({})   // server_id → {loading|tools|error}
  let drift = $state(null)
  let resyncMsg = $state(null)

  async function loadHealth() {
    try {
      const h = await api.mcpHealth()
      const map = {}
      for (const s of (h.servers ?? [])) map[s.server_id] = s
      healthMap = map
    } catch (_) { healthMap = {} }
  }
  async function loadUsage() {
    usageErr = ''
    try { const u = await api.mcpUsage(30); usage = u.rows ?? []; if (u.error) usageErr = 'Usage query failed (check UI logs).' }
    catch (e) { usageErr = e.message }
  }
  async function loadDrift() { try { drift = await api.drift() } catch (_) { drift = null } }
  onMount(() => { loadHealth(); loadUsage(); loadDrift() })

  let mcpDrift = $derived(drift?.mcp && !drift.mcp.error ? drift.mcp : null)
  let mcpDriftCount = $derived(mcpDrift
    ? (mcpDrift.missing_in_litellm?.length || 0) + (mcpDrift.extra_in_litellm?.length || 0) + (mcpDrift.content_drifted?.length || 0)
    : 0)

  // refresh live views after a successful Apply (same pattern as Models.svelte)
  let _prevApplying = false
  $effect(() => {
    const cur = store.applying
    if (_prevApplying && !cur && !store.error) { loadHealth(); loadUsage(); loadDrift() }
    _prevApplying = cur
  })

  async function resyncToProxy() {
    resyncMsg = null
    try {
      const r = await api.resync()
      const m = r.mcp || {}
      resyncMsg = { ok: true, text: `Resynced — MCP: ${m.added || 0} added, ${m.updated || 0} updated, ${m.deleted || 0} deleted${m.failed?.length ? `, ${m.failed.length} failed` : ''}.` }
    } catch (e) { resyncMsg = { ok: false, text: e.message } }
    await loadDrift(); await loadHealth()
  }

  function resetForm() {
    form = { server_name: '', description: '', transport: 'http', url: '', auth_type: '',
             auth_value: '', allow_all_keys: false, default_cost: '', hasStoredSecret: false }
    headerRows = []; extraRows = []; toolRows = []; costRows = []
    editingId = null; showAdd = false; formErr = ''
  }

  function editServer(item) {
    const d = item.data || {}
    const ci = d.mcp_info?.mcp_server_cost_info || {}
    form = { server_name: d.server_name || '', description: d.description || '',
             transport: d.transport || 'http', url: d.url || '',
             auth_type: d.auth_type || '', auth_value: '',
             allow_all_keys: !!d.allow_all_keys,
             default_cost: ci.default_cost_per_query ?? '',
             hasStoredSecret: !!d.auth_value_encrypted }
    headerRows = dictToHeaderRows(d.static_headers)
    extraRows = arrayToListRows(d.extra_headers)
    toolRows = arrayToListRows(d.allowed_tools)
    costRows = dictToCostRows(ci.tool_name_to_cost_per_query)
    editingId = item.name; showAdd = true; formErr = ''
  }

  async function saveServer() {
    formErr = ''
    const v = validateMcpForm(form)
    if (v) { formErr = v; return }
    const id = editingId || uuidv4()
    const ok = await store.stageItem('mcp_server', id, {
      server_name: form.server_name.trim(),
      description: form.description.trim(),
      transport: form.transport,
      url: form.url.trim(),
      auth_type: form.auth_type || null,
      auth_value: form.auth_value,          // blank on edit = keep stored secret (server-side)
      static_headers: headerRowsToDict(headerRows),
      extra_headers: listRowsToArray(extraRows),
      allowed_tools: listRowsToArray(toolRows),
      allow_all_keys: form.allow_all_keys,
      mcp_info: buildMcpInfo(form.default_cost, costRows),
    })
    if (ok) resetForm()   // keep input on a rejected save (422)
  }

  async function probeServer(item) {
    probing = { ...probing, [item.name]: true }
    try {
      const h = await api.mcpHealth(1, item.name)
      // probe is a bare array of {server_id, status} (Task 1 report (c))
      const entry = Array.isArray(h.probe) ? h.probe.find(p => p.server_id === item.name) : null
      const ok = !h.probe_error && entry?.status === 'healthy'
      probeRes = { ...probeRes, [item.name]: ok ? { ok: true, msg: 'Healthy' }
                 : { ok: false, msg: h.probe_error || entry?.status || 'no probe result' } }
      await loadHealth()
    } catch (e) {
      probeRes = { ...probeRes, [item.name]: { ok: false, msg: e.message } }
    } finally {
      probing = { ...probing, [item.name]: false }
    }
  }

  async function toggleTools(item) {
    if (toolsOpen === item.name) { toolsOpen = null; return }
    toolsOpen = item.name
    if (!toolsState[item.name]) {
      toolsState = { ...toolsState, [item.name]: { loading: true } }
      try {
        const r = await api.mcpTools(item.name)
        // access-denied arrives as HTTP 200 with an error field in the body (Task 1 report (h)7)
        if (r && !Array.isArray(r) && r.error) {
          toolsState = { ...toolsState, [item.name]: { error: r.message || r.error } }
        } else {
          const tools = Array.isArray(r) ? r : (r.tools ?? r.data ?? [])
          toolsState = { ...toolsState, [item.name]: { tools } }
        }
      } catch (e) {
        toolsState = { ...toolsState, [item.name]: { error: e.message } }
      }
    }
  }

  function addHeader() { headerRows = [...headerRows, { k: '', v: '' }] }
  function rmHeader(i) { headerRows = headerRows.filter((_, j) => j !== i) }
  function addExtra() { extraRows = [...extraRows, ''] }
  function rmExtra(i) { extraRows = extraRows.filter((_, j) => j !== i) }
  function addTool() { toolRows = [...toolRows, ''] }
  function rmTool(i) { toolRows = toolRows.filter((_, j) => j !== i) }
  function addCost() { costRows = [...costRows, { tool: '', cost: '' }] }
  function rmCost(i) { costRows = costRows.filter((_, j) => j !== i) }

  function healthInfo(item) {
    if (item.flag === 'new') return { color: '#c7c7cc', title: 'Not applied yet' }
    const h = healthMap[item.name]
    if (!h || !h.status || h.status === 'unknown') return { color: '#8e8e93', title: 'Health unknown — use Test' }
    if (h.status === 'healthy') return { color: '#34c759', title: `Healthy (checked ${h.last_health_check ? fmtDateTime(h.last_health_check) : '—'})` }
    return { color: '#ff3b30', title: h.health_check_error || h.status }
  }

  function flagAccent(flag) {
    if (flag === 'new') return 'row-new'
    if (flag === 'changed') return 'row-changed'
    if (flag === 'deleted') return 'row-deleted'
    return ''
  }
</script>

<div class="page">
  <header><h1>MCP Servers</h1>
    {#if mcpDrift}
      <span class="drift" class:ok={mcpDriftCount === 0} class:warn={mcpDriftCount > 0}
        title={mcpDriftCount === 0 ? 'ui_config and the proxy agree' : 'ui_config and the proxy differ'}>
        {mcpDriftCount === 0 ? 'In sync ✓' : `⚠ ${mcpDriftCount} out of sync`}
      </span>
      {#if mcpDriftCount > 0}
        <button onclick={resyncToProxy} disabled={store.applying || store.saving}>Resync to proxy</button>
      {/if}
    {/if}
    <button class="primary" onclick={() => { editingId = null; showAdd = !showAdd; formErr = '' }} disabled={store.applying}>＋ Add MCP server</button>
  </header>
  {#if resyncMsg}<div class="banner {resyncMsg.ok ? 'ok' : 'err'}">{resyncMsg.text}</div>{/if}
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}

  {#if showAdd}
    <div class="card add">
      <h3 style="margin:0 0 4px">{editingId ? 'Edit MCP server' : 'Add MCP server'}</h3>
      <label>Server name <input bind:value={form.server_name} placeholder="e.g. firecrawl" />
        <span class="hint">Letters, digits, _ or -. Becomes the tool prefix (<code>firecrawl-scrape</code>) and the per-server endpoint (<code>/firecrawl/mcp</code>).</span>
      </label>
      <label>Description <input bind:value={form.description} placeholder="optional" /></label>
      <label>Transport
        <select bind:value={form.transport}>
          <option value="http">Streamable HTTP</option>
          <option value="sse">SSE</option>
        </select>
      </label>
      <label>URL <input bind:value={form.url} placeholder="http://10.0.20.x:3002/mcp" />
        <span class="hint">⚠ Stored and displayed in plain text — if a vendor embeds the API key in the URL, prefer header auth or a self-hosted instance.</span>
      </label>
      <label>Auth
        <select bind:value={form.auth_type}>
          <option value="">None</option>
          <option value="api_key">API key</option>
          <option value="bearer_token">Bearer token</option>
          <option value="basic">Basic</option>
        </select>
      </label>
      {#if form.auth_type}
        <label>Auth value
          <input type="password" bind:value={form.auth_value}
                 placeholder={form.hasStoredSecret ? '(unchanged — leave blank to keep)' : 'secret'} />
          <span class="hint">Encrypted at rest; never shown again. Blank on edit keeps the stored secret.</span>
        </label>
      {/if}
      <div class="rows">
        <span class="field-name">Static headers <span class="hint">(sent on every request — no secrets here, use Auth)</span></span>
        {#each headerRows as row, i}
          <div class="kv-row">
            <input placeholder="Header" bind:value={row.k} />
            <input placeholder="value" bind:value={row.v} />
            <button type="button" class="x" onclick={() => rmHeader(i)} aria-label="remove header">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addHeader}>+ Add header</button>
      </div>
      <div class="rows">
        <span class="field-name">Forwarded client headers</span>
        {#each extraRows as _, i}
          <div class="kv-row">
            <input placeholder="Header name (e.g. Authorization)" bind:value={extraRows[i]} />
            <button type="button" class="x" onclick={() => rmExtra(i)} aria-label="remove">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addExtra}>+ Add forwarded header</button>
      </div>
      <div class="rows">
        <span class="field-name">Allowed tools <span class="hint">(blank = all tools exposed)</span></span>
        {#each toolRows as _, i}
          <div class="kv-row">
            <input placeholder="tool name" bind:value={toolRows[i]} />
            <button type="button" class="x" onclick={() => rmTool(i)} aria-label="remove">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addTool}>+ Add tool</button>
      </div>
      <label class="check"><input type="checkbox" bind:checked={form.allow_all_keys} />
        Allow all virtual keys
        <span class="hint">Every key may use this server without an explicit grant on the Keys page.</span>
      </label>
      <label>Default cost per tool call ($) <input type="number" min="0" step="0.001" bind:value={form.default_cost} placeholder="0 = free" /></label>
      <div class="rows">
        <span class="field-name">Per-tool cost overrides</span>
        {#each costRows as row, i}
          <div class="kv-row">
            <input placeholder="tool name" bind:value={row.tool} />
            <input type="number" min="0" step="0.001" placeholder="$ per call" bind:value={row.cost} />
            <button type="button" class="x" onclick={() => rmCost(i)} aria-label="remove">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addCost}>+ Add tool cost</button>
      </div>
      {#if formErr}<div class="banner err">{formErr}</div>{/if}
      <div class="row">
        <button class="primary" onclick={saveServer} disabled={store.saving || store.applying}>Save</button>
        <button onclick={resetForm}>Cancel</button>
      </div>
      <p class="hint">Saved changes are staged — click <strong>Apply</strong> to push them to the gateway (hot, no proxy restart).</p>
    </div>
  {/if}

  <div class="card">
    {#if mcpItems.length === 0}<p class="empty">No MCP servers yet. Add one to unify your MCP tools behind the proxy.</p>
    {:else}
      <table>
        <thead><tr><th>Name</th><th>Transport</th><th>URL</th><th>Auth</th><th>Keys</th><th>Health</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {#each mcpItems as item (item.name)}
            {@const d = item.data || {}}
            {@const flag = item.flag}
            {@const dot = healthInfo(item)}
            {@const pr = probeRes[item.name]}
            <tr class={flagAccent(flag)}>
              <td class:strikethrough={flag === 'deleted'}><strong>{d.server_name}</strong>
                {#if d.description}<div class="hint">{d.description}</div>{/if}</td>
              <td class:strikethrough={flag === 'deleted'}>{d.transport}</td>
              <td class:strikethrough={flag === 'deleted'} class="trunc" title={d.url}><code>{d.url}</code></td>
              <td>{d.auth_type || '—'}</td>
              <td>{d.allow_all_keys ? 'all' : 'granted'}</td>
              <td><span class="dot" style="background:{dot.color}" title={dot.title}></span>
                {#if flag !== 'deleted'}
                  <button class="small" onclick={() => probeServer(item)} disabled={probing[item.name]}>{probing[item.name] ? '…' : 'Test'}</button>
                  {#if pr}<span class="check-res" class:ok={pr.ok} class:bad={!pr.ok} title={pr.msg}>{pr.ok ? '✓' : '✗'}</span>{/if}
                {/if}
              </td>
              <td>
                {#if flag === 'new'}<span class="flag-tag flag-new">new</span>
                {:else if flag === 'changed'}<span class="flag-tag flag-changed">changed</span>
                {:else if flag === 'deleted'}<span class="flag-tag flag-deleted">deleted</span>{/if}
              </td>
              <td class="actions">
                {#if flag === 'deleted'}
                  <button class="undo" onclick={() => store.discard('mcp_server', item.name)} disabled={store.saving || store.applying}>Undo</button>
                {:else}
                  <button class="small" onclick={() => toggleTools(item)} disabled={flag === 'new'}
                          title={flag === 'new' ? 'Apply first — tools come from the live gateway' : 'List tools this server exposes'}>
                    {toolsOpen === item.name ? 'Hide tools' : 'Tools'}
                  </button>
                  <button onclick={() => editServer(item)} disabled={store.saving || store.applying}>Edit</button>
                  <button class="danger" onclick={() => store.deleteItem('mcp_server', item.name)} disabled={store.saving || store.applying}>Delete</button>
                {/if}
              </td>
            </tr>
            {#if toolsOpen === item.name}
              {@const ts = toolsState[item.name]}
              <tr class="detail-row"><td colspan="8">
                {#if ts?.loading}<p class="empty">Loading tools…</p>
                {:else if ts?.error}<p class="empty">Couldn't list tools — {ts.error}</p>
                {:else if ts?.tools}
                  {#if ts.tools.length === 0}<p class="empty">No tools exposed.</p>
                  {:else}
                    <ul class="tool-list">
                      {#each ts.tools as t}<li><code>{t.name}</code>{#if t.description} — <span class="hint">{t.description}</span>{/if}</li>{/each}
                    </ul>
                  {/if}
                {/if}
              </td></tr>
            {/if}
          {/each}
        </tbody>
      </table>
    {/if}
  </div>

  <div class="card">
    <h3 style="margin:0 0 8px">Usage (last 30 days)</h3>
    {#if usageErr}<div class="banner err">{usageErr}</div>{/if}
    {#if usage.length === 0}<p class="empty">No MCP tool calls recorded yet.</p>
    {:else}
      <table>
        <thead><tr><th>Server</th><th>Calls</th><th>Failures</th><th>Spend</th><th>Last call</th></tr></thead>
        <tbody>
          {#each usage as u}
            <tr>
              <td>{u.server}</td>
              <td>{u.calls.toLocaleString()}</td>
              <td class:red={u.failures > 0}>{u.failures}</td>
              <td>{money(u.spend)}</td>
              <td class="nowrap">{fmtDateTime(u.last_call)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>

  <div class="card">
    <h3 style="margin:0 0 8px">Connecting clients</h3>
    <p class="hint" style="font-size:13px">
      Point MCP clients at <code>http://&lt;proxy-host&gt;:8000/mcp</code> (streamable HTTP) with header
      <code>Authorization: Bearer &lt;virtual key&gt;</code> — the protocol endpoint rejects
      x-litellm-api-key on this build (Task 1 report (g)). Scope with
      <code>x-mcp-servers: name1,name2</code>, or use a per-server endpoint
      <code>/&lt;server_name&gt;/mcp</code>. Tools are namespaced <code>&lt;server_name&gt;-&lt;tool&gt;</code>
      on the protocol endpoint. Grant keys access on the <strong>Virtual Keys</strong> page —
      grants and revokes take up to ~60s to propagate (auth cache TTL).
    </p>
  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:1000px}
  header{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.add{display:flex;flex-direction:column;gap:10px;max-width:560px}
  label{display:flex;flex-direction:column;font-size:13px;color:#3a3a3c;gap:4px}
  input,select{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .row{display:flex;gap:8px;margin-top:4px}
  button{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}
  button.danger{color:#ff3b30;border-color:#ffd0cc}
  button.undo{color:#ff9500;border-color:#ffe0b2}
  button.small{padding:4px 10px;font-size:12px}
  button:disabled{opacity:.5;cursor:default}
  .banner{padding:10px 12px;border-radius:8px;margin-top:8px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}
  .hint{font-size:11px;color:#6e6e73}
  .empty{color:#6e6e73}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .check-res{margin-left:6px;font-weight:600}
  .check-res.ok{color:#34c759}.check-res.bad{color:#ff3b30}
  .actions{display:flex;gap:6px;flex-wrap:wrap}
  .rows{display:flex;flex-direction:column;gap:4px}
  .field-name{font-size:13px;color:#3a3a3c}
  .kv-row{display:flex;gap:6px;align-items:center}
  .kv-row input{flex:1;min-width:0}
  .x{border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer;padding:4px 9px}
  .addrow{margin-top:2px;font-size:12px;padding:3px 12px;border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer;width:fit-content}
  label.check{flex-direction:row;align-items:flex-start;gap:8px;flex-wrap:wrap}
  label.check input{margin-top:2px}
  .drift{font-size:12px;padding:3px 10px;border-radius:20px}
  .drift.ok{background:#e7f7ec;color:#1d7a33}
  .drift.warn{background:#fff4e5;color:#9a5b00}
  .row-new{background:rgba(10,132,255,.06)}
  .row-changed{background:rgba(255,149,0,.06)}
  .row-deleted{background:rgba(255,59,48,.05)}
  .strikethrough{text-decoration:line-through;color:#8e8e93}
  .flag-tag{display:inline-block;font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;text-transform:uppercase;letter-spacing:.04em}
  .flag-new{background:rgba(10,132,255,.12);color:#0a52c7}
  .flag-changed{background:rgba(255,149,0,.15);color:#b36800}
  .flag-deleted{background:rgba(255,59,48,.12);color:#c0271d}
  .detail-row td{background:#fafafc;white-space:normal}
  .tool-list{margin:6px 0;padding-left:18px;font-size:13px}
  .trunc{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .nowrap{white-space:nowrap}
  .red{color:#c0271d}
</style>
