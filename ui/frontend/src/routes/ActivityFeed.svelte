<script>
  import { api } from '../lib/api.js'
  import { copyText } from '../lib/browser.js'
  import { money, fmtMs } from '../lib/format.js'

  let { days, byModel = [], byKey = [], refreshTick = 0 } = $props()

  function initMode() { return localStorage.getItem('usage.activityMode') === 'history' ? 'history' : 'recent' }
  let mode = $state(initMode())
  $effect(() => localStorage.setItem('usage.activityMode', mode))

  // History filters (session-scoped, not persisted)
  let fStatus = $state('all')
  let fModel = $state('')
  let fKey = $state('')

  let rows = $state([])
  let stats = $state(null)
  let nextCursor = $state(null)
  let busy = $state(false)
  let feedErr = $state('')

  let openId = $state(null)
  let detail = $state({})   // id → { loading?|data?|error? }

  const qs = (p) => Object.entries(p)
    .filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')

  async function loadFirst(reset = true) {
    busy = true; feedErr = ''
    try {
      const p = { days, limit: 50 }
      if (mode === 'history') Object.assign(p, { status: fStatus, model: fModel, key: fKey, stats: 1 })
      const d = await api.get(`/api/usage/activity?${qs(p)}`)
      if (d.error) { feedErr = "Couldn't load activity — the query failed (check the UI logs). This is not the same as no activity."; rows = []; stats = null; nextCursor = null; return }
      rows = d.rows ?? []
      nextCursor = mode === 'history' ? (d.next_cursor ?? null) : null
      stats = d.stats ?? null
      if (reset) { openId = null; detail = {} }
      else if (openId && !rows.some(r => r.id === openId)) openId = null
    } catch (e) { feedErr = e.message; rows = [] }
    finally { busy = false }
  }

  async function loadMore() {
    if (!nextCursor || busy) return
    busy = true; feedErr = ''
    try {
      const p = { days, limit: 50, status: fStatus, model: fModel, key: fKey, cursor: nextCursor }
      const d = await api.get(`/api/usage/activity?${qs(p)}`)
      if (d.error) { feedErr = "Couldn't load more — the query failed."; return }
      rows = [...rows, ...(d.rows ?? [])]
      nextCursor = d.next_cursor ?? null
    } catch (e) { feedErr = e.message }
    finally { busy = false }
  }

  // mode / window / filter change → full reload (collapses detail)
  $effect(() => { mode; days; fStatus; fModel; fKey; loadFirst(true) })
  // auto-refresh signal from the host: silently refresh Recent only (History never rug-pulls)
  let _prevTick = 0
  $effect(() => {
    const t = refreshTick
    if (t !== _prevTick) { _prevTick = t; if (mode === 'recent') loadFirst(false) }
  })

  async function toggle(id) {
    if (openId === id) { openId = null; return }
    openId = id
    if (!detail[id]) {
      detail = { ...detail, [id]: { loading: true } }
      try {
        const d = await api.get(`/api/usage/tx/${encodeURIComponent(id)}`)
        detail = { ...detail, [id]: { data: d } }
      } catch (e) {
        detail = { ...detail, [id]: { error: e.message } }
      }
    }
  }
  function retry(id) { detail = { ...detail, [id]: undefined }; openId = null; toggle(id) }

  function timing(d) {
    const parts = []
    if (d.ttft_ms != null) parts.push(`TTFT ${fmtMs(d.ttft_ms)}`)
    if (d.gen_ms != null) parts.push(`generation ${fmtMs(d.gen_ms)}`)
    parts.push(`total ${fmtMs(d.latency_ms)}`)
    return parts.join(' · ')
  }
</script>

<div class="card">
  <div class="feed-head">
    <h2>Activity</h2>
    <div class="seg">
      <button class="seg-btn" class:active={mode === 'recent'} onclick={() => mode = 'recent'}>Recent</button>
      <button class="seg-btn" class:active={mode === 'history'} onclick={() => mode = 'history'}>History</button>
    </div>
  </div>

  {#if mode === 'history'}
    <div class="chips">
      <div class="seg small">
        {#each [['all','All'],['success','Success'],['failure','Failure']] as [v, label]}
          <button class="seg-btn" class:active={fStatus === v} onclick={() => fStatus = v}>{label}</button>
        {/each}
      </div>
      <select bind:value={fModel} aria-label="filter model">
        <option value="">All models</option>
        {#each byModel as m}<option value={m}>{m}</option>{/each}
      </select>
      <select bind:value={fKey} aria-label="filter key">
        <option value="">All keys</option>
        {#each byKey as k}<option value={k}>{k}</option>{/each}
      </select>
    </div>
    {#if stats}
      <div class="strip">
        <span class="pill">{stats.count.toLocaleString()} requests</span>
        <span class="pill" class:red={stats.err_pct > 0}>err {stats.err_pct.toFixed(1)}%</span>
        <span class="pill">p50 {fmtMs(stats.p50_ms)}</span>
        <span class="pill">p90 {fmtMs(stats.p90_ms)}</span>
        <span class="pill">p95 {fmtMs(stats.p95_ms)}</span>
        <span class="pill">p99 {fmtMs(stats.p99_ms)}</span>
      </div>
    {/if}
  {/if}

  {#if feedErr}<div class="banner err">{feedErr}</div>{/if}

  {#if rows.length === 0 && !busy && !feedErr}
    <p class="empty">No activity in this range.</p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Model</th><th>Provider</th><th>Key</th>
            <th>Tok in</th><th>Tok out</th><th>Spend</th><th>Latency</th><th>Status</th><th>Cache</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r (r.id)}
            <tr class="row" class:failed={r.status === 'failure'} class:open={openId === r.id}
                onclick={() => toggle(r.id)}>
              <td class="nowrap">{new Date(r.time).toLocaleTimeString()}</td>
              <td class="trunc" title={r.model}>{r.model || '—'}</td>
              <td>{r.provider || '—'}</td>
              <td>{r.key}</td>
              <td>{(r.tok_in ?? 0).toLocaleString()}</td>
              <td>{(r.tok_out ?? 0).toLocaleString()}</td>
              <td>{money(r.spend)}</td>
              <td>{fmtMs(r.latency_ms)}</td>
              <td class:green={r.status === 'success'} class:red={r.status !== 'success'}>
                {r.status === 'success' ? '✓' : '✗ failed'}
              </td>
              <td>{r.cache_hit === true ? 'hit' : r.cache_hit === false ? 'miss' : '—'}</td>
            </tr>
            {#if openId === r.id}
              {@const d = detail[r.id]}
              <tr class="detail-row"><td colspan="10">
                {#if d?.loading}<p class="empty">Loading detail…</p>
                {:else if d?.error}<p class="empty">Couldn't load detail — {d.error} <button class="linkbtn" onclick={() => retry(r.id)}>retry</button></p>
                {:else if d?.data}
                  {@const t = d.data}
                  <div class="dgrid">
                    <span class="dl">Request</span>
                    <span class="dv mono">{t.id} <button class="linkbtn" onclick={(e) => { e.stopPropagation(); copyText(t.id) }}>Copy</button></span>
                    <span class="dl">Call</span><span class="dv">{t.call_type || '—'}</span>
                    <span class="dl">Route</span>
                    <span class="dv">{t.model_group || '—'} → {t.model || '—'}{t.provider ? ` (${t.provider})` : ''}</span>
                    {#if t.api_base}<span class="dl">API base</span><span class="dv mono">{t.api_base}</span>{/if}
                    <span class="dl">Tokens</span><span class="dv">{t.tok_in.toLocaleString()} in / {t.tok_out.toLocaleString()} out / {t.tok_total.toLocaleString()} total</span>
                    <span class="dl">Spend</span>
                    <span class="dv">{money(t.spend)}{t.cost_per_1m != null ? ` · $${t.cost_per_1m.toFixed(4)}/1M` : ''}</span>
                    <span class="dl">Timing</span><span class="dv">{timing(t)}</span>
                    <span class="dl">Cache</span><span class="dv">{t.cache_hit === true ? 'hit' : t.cache_hit === false ? 'miss' : '—'}</span>
                    {#if t.session_id}<span class="dl">Session</span><span class="dv mono">{t.session_id}</span>{/if}
                    {#if t.end_user}<span class="dl">End user</span><span class="dv">{t.end_user}</span>{/if}
                    {#if t.tags.length}<span class="dl">Tags</span><span class="dv">{t.tags.join(', ')}</span>{/if}
                  </div>
                  {#if t.error}
                    <div class="errbox">
                      <div class="errhead">{t.error.class || 'Error'}{t.error.code ? ` (${t.error.code})` : ''}{t.error.provider ? ` — ${t.error.provider}` : ''}</div>
                      <div class="errmsg">{t.error.message}</div>
                      {#if t.error.traceback}
                        <details><summary>Traceback</summary><pre>{t.error.traceback}</pre></details>
                      {/if}
                    </div>
                  {/if}
                {/if}
              </td></tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
    {#if mode === 'history' && nextCursor}
      <div class="more"><button class="fb-add" onclick={loadMore} disabled={busy}>{busy ? 'Loading…' : 'Load more'}</button></div>
    {/if}
  {/if}
</div>

<style>
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  h2{font-size:15px;margin:0}
  .feed-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
  .seg{display:flex;gap:6px}
  .seg-btn{padding:4px 14px;border:1px solid rgba(0,0,0,.15);border-radius:20px;background:#f5f5f7;font-size:13px;cursor:pointer;transition:background .15s}
  .seg-btn:hover{background:#e5e5ea}
  .seg-btn.active{background:#0a84ff;color:#fff;border-color:#0a84ff}
  .seg.small .seg-btn{padding:3px 10px;font-size:12px}
  .chips{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
  .chips select{font-size:13px;padding:4px 8px;border:1px solid rgba(0,0,0,.15);border-radius:8px;background:#fff}
  .strip{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
  .pill{font-size:12px;padding:3px 10px;border-radius:20px;background:#f5f5f7;color:#3a3a3c}
  .pill.red{background:#ffeceb;color:#c0271d}
  .table-wrap{overflow-x:auto}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:13px;white-space:nowrap}
  tr.row{cursor:pointer}
  tr.row:hover td{background:rgba(0,0,0,.03)}
  tr.row.failed td{background:#fff7f6}
  tr.row.open td{background:rgba(10,132,255,.06)}
  .detail-row td{background:#fafafc;white-space:normal}
  .dgrid{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;padding:6px 2px;font-size:13px}
  .dl{color:#6e6e73}
  .dv{overflow-wrap:anywhere}
  .mono{font-family:"SF Mono","Fira Code",monospace;font-size:12px}
  .errbox{margin:8px 2px 4px;padding:10px 12px;background:#ffeceb;border-radius:8px;font-size:13px}
  .errhead{font-weight:600;color:#c0271d}
  .errmsg{margin-top:4px;color:#3a3a3c;overflow-wrap:anywhere}
  .errbox pre{margin:6px 0 0;max-height:240px;overflow:auto;font-size:11px;white-space:pre-wrap}
  .errbox summary{cursor:pointer;font-size:12px;color:#6e6e73;margin-top:6px}
  .more{margin-top:10px;text-align:center}
  .fb-add{font-size:12px;padding:4px 12px;border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer}
  .linkbtn{background:none;border:0;padding:0;color:#0a84ff;cursor:pointer;font:inherit;font-size:12px;text-decoration:underline}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin:6px 0;font-size:13px}
  .empty{color:#6e6e73}
  .red{color:#c0271d}
  .green{color:#1a7f37}
  .trunc{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .nowrap{white-space:nowrap}
</style>
