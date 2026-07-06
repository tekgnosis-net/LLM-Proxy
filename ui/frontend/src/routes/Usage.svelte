<script>
  import { onMount, onDestroy } from 'svelte'
  import { api } from '../lib/api.js'
  import Chart from '../lib/Chart.svelte'
  import ActivityFeed from './ActivityFeed.svelte'
  import { money, fmtMs } from '../lib/format.js'

  // ── v3.8 range/refresh persistence (PRESERVED) ────────────────────────────
  function initDays() { const v = +localStorage.getItem('usage.days'); return [1,7,30,90].includes(v) ? v : 30 }
  function initRefresh() { return +localStorage.getItem('usage.refreshSec') || 0 }  // 0 = off
  let days = $state(initDays())
  let refreshSec = $state(initRefresh())
  let timer = null

  let summary = $state(null)
  let err = $state('')
  let loading = $state(true)

  let refreshTick = $state(0)

  async function load(silent = false) {
    if (!silent) { loading = true; summary = null }
    err = ''
    try {
      summary = await api.get(`/api/usage/summary?days=${days}`)
      if (silent) refreshTick++          // nudge ActivityFeed (Recent mode only)
    } catch (e) { err = e.message }
    finally { if (!silent) loading = false }
  }

  $effect(() => { localStorage.setItem('usage.days', days); load() })          // range change → save + reload
  $effect(() => { localStorage.setItem('usage.refreshSec', refreshSec); arm() }) // interval change → save + re-arm
  function arm() {
    if (timer) { clearInterval(timer); timer = null }
    if (refreshSec > 0 && !document.hidden) timer = setInterval(() => load(true), refreshSec * 1000)
  }
  function onVis() { arm() }                       // pause when hidden, resume when visible
  onMount(() => document.addEventListener('visibilitychange', onVis))
  onDestroy(() => { if (timer) clearInterval(timer); document.removeEventListener('visibilitychange', onVis) })

  // ── chart data derived from summary.timeseries ────────────────────────────
  const chartSeries = [
    {},
    { label: 'Requests', stroke: '#0a84ff' },
    { label: 'Spend $',  stroke: '#34c759', scale: '$' },
    { label: 'p95 ms',   stroke: '#ff9f0a', scale: 'ms' },
  ]
  function chartData(ts) {
    if (!ts || ts.length === 0) return [[],[],[],[]]
    const xs     = ts.map(t => new Date(t.bucket).getTime() / 1000)
    const reqs   = ts.map(t => t.requests ?? 0)
    const spends = ts.map(t => t.spend ?? 0)
    const p95s   = ts.map(t => t.p95_ms ?? null)
    return [xs, reqs, spends, p95s]
  }

  // ── breakdown tabs ────────────────────────────────────────────────────────
  let tab = $state('provider')

  // per-tab sort state
  let sortCol = $state('requests')
  let sortDir = $state(-1)   // -1 = desc, 1 = asc

  function tabRows() {
    const src = tab === 'provider' ? (summary?.by_provider ?? [])
              : tab === 'model'    ? (summary?.by_model    ?? [])
              :                      (summary?.by_key      ?? [])
    return [...src].sort((a, b) => {
      const av = a[sortCol] ?? -Infinity
      const bv = b[sortCol] ?? -Infinity
      return typeof av === 'string'
        ? sortDir * av.localeCompare(bv)
        : sortDir * (bv - av)   // numeric: desc = larger first when sortDir=-1
    })
  }
  function setSort(col) {
    if (sortCol === col) sortDir = -sortDir
    else { sortCol = col; sortDir = -1 }
  }
  function sortIcon(col) { return sortCol === col ? (sortDir === -1 ? '▼' : '▲') : '' }

  const rangeLabel = (n) => n === 1 ? '24h' : `${n}d`
</script>

<div class="page">
  <h1>Usage &amp; Spend</h1>

  <!-- ── range + auto-refresh (v3.8 preserved) ── -->
  <div class="range-row">
    {#each [1, 7, 30, 90] as n}
      <button class="range-btn" class:active={days === n} onclick={() => days = n}>{rangeLabel(n)}</button>
    {/each}
    <label class="refresh">Auto-refresh
      <select bind:value={refreshSec}>
        <option value={0}>Off</option><option value={10}>10s</option><option value={30}>30s</option>
        <option value={60}>60s</option><option value={300}>5m</option>
      </select>
    </label>
  </div>

  <!-- ── error banners (v3.8 preserved, d renamed to summary) ── -->
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if summary?.error}<div class="banner err">Couldn't load usage — the query failed (check the UI logs). This is not the same as "no usage".</div>{/if}

  {#if loading}<p class="empty">Loading…</p>
  {:else if summary}

    <!-- ── KPI row ── -->
    <div class="cards">
      <div class="card stat">
        <div class="label">Total spend</div>
        <div class="big">{money(summary.kpis?.spend)}</div>
      </div>
      <div class="card stat">
        <div class="label">Requests</div>
        <div class="big">{(summary.kpis?.requests ?? 0).toLocaleString()}</div>
      </div>
      <div class="card stat">
        <div class="label">Tokens</div>
        <div class="big">{(summary.kpis?.tok_in ?? 0).toLocaleString()} in / {(summary.kpis?.tok_out ?? 0).toLocaleString()} out</div>
      </div>
      <div class="card stat">
        <div class="label">Error rate</div>
        <div class="big" class:red={summary.kpis?.error_rate > 0}>
          {((summary.kpis?.error_rate ?? 0) * 100).toFixed(1)}%
        </div>
      </div>
      <div class="card stat">
        <div class="label">Avg latency</div>
        <div class="big">{fmtMs(summary.kpis?.avg_latency_ms)}</div>
      </div>
      <div class="card stat">
        <div class="label">p95 latency</div>
        <div class="big">{fmtMs(summary.kpis?.p95_latency_ms)}</div>
      </div>
      <div class="card stat">
        <div class="label">Cache hit (of all req)</div>
        <div class="big">
          {summary.kpis?.cache_hit_rate == null
            ? '—'
            : (summary.kpis.cache_hit_rate * 100).toFixed(2) + '%'}
        </div>
      </div>
    </div>

    <!-- ── Time-series chart ── -->
    {#if (summary.timeseries ?? []).length > 0}
      <div class="card">
        <h2>Activity over time <span class="gran">({summary.granularity})</span></h2>
        <Chart data={chartData(summary.timeseries)} series={chartSeries} height={240} />
      </div>
    {/if}

    <!-- ── Breakdown tabs ── -->
    <div class="card">
      <div class="tab-row">
        {#each [['provider','By provider'],['model','By model'],['key','By key']] as [id, label]}
          <button class="tab-btn" class:active={tab === id}
            onclick={() => { tab = id; sortCol = 'requests'; sortDir = -1 }}>{label}</button>
        {/each}
      </div>

      {#if tabRows().length === 0}
        <p class="empty">No data in this range.</p>
      {:else}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th onclick={() => setSort('label')} class="sortable">Label {sortIcon('label')}</th>
                <th onclick={() => setSort('requests')} class="sortable">Requests {sortIcon('requests')}</th>
                <th onclick={() => setSort('tok_in')} class="sortable">Tok in {sortIcon('tok_in')}</th>
                <th onclick={() => setSort('tok_out')} class="sortable">Tok out {sortIcon('tok_out')}</th>
                <th onclick={() => setSort('spend')} class="sortable">Spend {sortIcon('spend')}</th>
                <th onclick={() => setSort('cost_per_1m')} class="sortable">Cost/1M {sortIcon('cost_per_1m')}</th>
                <th onclick={() => setSort('p50_ms')} class="sortable">p50 {sortIcon('p50_ms')}</th>
                <th onclick={() => setSort('p95_ms')} class="sortable">p95 {sortIcon('p95_ms')}</th>
                <th onclick={() => setSort('err_pct')} class="sortable">Err% {sortIcon('err_pct')}</th>
                {#if tab === 'key'}
                  <th onclick={() => setSort('last_used')} class="sortable">Last used {sortIcon('last_used')}</th>
                {/if}
              </tr>
            </thead>
            <tbody>
              {#each tabRows() as row}
                <tr>
                  <td>
                    {#if row.label === '(none)'}
                      <span class="muted">failed / no backend</span>
                    {:else}
                      {row.label}
                    {/if}
                  </td>
                  <td>{(row.requests ?? 0).toLocaleString()}</td>
                  <td>{(row.tok_in ?? 0).toLocaleString()}</td>
                  <td>{(row.tok_out ?? 0).toLocaleString()}</td>
                  <td>{money(row.spend)}</td>
                  <td>{row.cost_per_1m == null ? '—' : '$' + row.cost_per_1m.toFixed(4)}</td>
                  <td>{fmtMs(row.p50_ms)}</td>
                  <td>{fmtMs(row.p95_ms)}</td>
                  <td class:red={row.err_pct > 0}>{(row.err_pct ?? 0).toFixed(1)}%</td>
                  {#if tab === 'key'}
                    <td>{row.last_used ? new Date(row.last_used).toLocaleString() : '—'}</td>
                  {/if}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </div>

    <!-- ── Activity feed (Recent | History) ── -->
    <ActivityFeed {days} {refreshTick}
      byModel={(summary.by_model ?? []).map(r => r.label).filter(l => l && l !== '(none)')}
      byKey={(summary.by_key ?? []).map(r => r.label).filter(Boolean)} />

  {/if}
</div>

<style>
  .page{padding:24px 30px;max-width:1100px}
  .range-row{display:flex;align-items:center;gap:8px;margin:12px 0 4px}
  .refresh{margin-left:auto;font-size:13px;color:#6e6e73}
  .range-btn{padding:5px 16px;border:1px solid rgba(0,0,0,.15);border-radius:8px;background:#f5f5f7;font-size:13px;cursor:pointer;transition:background .15s}
  .range-btn:hover{background:#e5e5ea}
  .range-btn.active{background:#0a84ff;color:#fff;border-color:#0a84ff}
  .cards{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.stat{flex:1;min-width:120px;margin-top:0}
  .label{font-size:12px;color:#6e6e73}
  .big{font-size:22px;font-weight:600;margin-top:6px}
  h2{font-size:15px;margin:0 0 10px}
  .gran{font-size:12px;font-weight:400;color:#6e6e73}
  .tab-row{display:flex;gap:6px;margin-bottom:12px}
  .tab-btn{padding:4px 14px;border:1px solid rgba(0,0,0,.15);border-radius:20px;background:#f5f5f7;font-size:13px;cursor:pointer;transition:background .15s}
  .tab-btn:hover{background:#e5e5ea}
  .tab-btn.active{background:#0a84ff;color:#fff;border-color:#0a84ff}
  .table-wrap{overflow-x:auto}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:13px;white-space:nowrap}
  th.sortable{cursor:pointer;user-select:none}
  th.sortable:hover{background:rgba(0,0,0,.04)}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
  .empty{color:#6e6e73}
  .muted{color:#a0a0a5;font-style:italic}
  .red{color:#c0271d}
</style>
