<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'

  let days = $state(30)
  let d = $state(null)
  let err = $state('')
  let loading = $state(true)

  async function load() {
    loading = true; err = ''; d = null
    try { d = await api.get(`/api/usage/summary?days=${days}`) }
    catch (e) { err = e.message }
    finally { loading = false }
  }

  onMount(load)
  $effect(() => { days; load() })

  const money = (n) => `$${Number(n ?? 0).toFixed(4)}`
  function maxReq() { return Math.max(1, ...((d?.daily ?? []).map(x => x.requests ?? 0))) }
</script>

<div class="page">
  <h1>Usage &amp; Spend</h1>

  <div class="range-row">
    {#each [7, 30, 90] as n}
      <button class="range-btn" class:active={days === n} onclick={() => days = n}>{n}d</button>
    {/each}
  </div>

  {#if err}<div class="banner err">{err}</div>{/if}
  {#if d?.error}<div class="banner err">Couldn't load usage — the query failed (check the UI logs). This is not the same as "no usage".</div>{/if}

  {#if loading}<p class="empty">Loading…</p>
  {:else if d}
    <div class="cards">
      <div class="card stat"><div class="label">Total spend</div><div class="big">{money(d.totals?.spend)}</div></div>
      <div class="card stat"><div class="label">Requests</div><div class="big">{d.totals?.requests ?? 0}</div></div>
      <div class="card stat"><div class="label">Tokens</div><div class="big">{(d.totals?.tokens ?? 0).toLocaleString()}</div></div>
    </div>

    <div class="card">
      <h2>Spend by model</h2>
      {#if (d.by_model ?? []).length === 0}
        <p class="empty">No usage in this range.</p>
      {:else}
        <table><thead><tr><th>Model</th><th>Spend</th><th>Requests</th><th>Tokens</th></tr></thead>
        <tbody>
          {#each d.by_model as m}
            <tr><td>{m.model}</td><td>{money(m.spend)}</td><td>{m.requests}</td><td>{(m.tokens ?? 0).toLocaleString()}</td></tr>
          {/each}
        </tbody></table>
      {/if}
    </div>

    <div class="card">
      <h2>Spend by key</h2>
      {#if (d.by_key ?? []).length === 0}
        <p class="empty">No usage in this range.</p>
      {:else}
        <table><thead><tr><th>Key</th><th>Spend</th><th>Requests</th><th>Last used</th></tr></thead>
        <tbody>
          {#each d.by_key as k}
            <tr>
              <td>{k.key}</td>
              <td>{money(k.spend)}</td>
              <td>{k.requests}</td>
              <td>{k.last_used ? new Date(k.last_used).toLocaleDateString() : '—'}</td>
            </tr>
          {/each}
        </tbody></table>
      {/if}
    </div>

    <div class="card">
      <h2>Daily activity</h2>
      {#if (d.daily ?? []).length === 0}
        <p class="empty">No usage in this range.</p>
      {:else}
        <div class="spark">
          {#each d.daily as x}
            <div class="col" title="{x.day}: {x.requests} req, ${Number(x.spend ?? 0).toFixed(4)}">
              <div class="colfill" style="height:{Math.round((x.requests ?? 0)/maxReq()*100)}%"></div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .page{padding:24px 30px;max-width:960px}
  .range-row{display:flex;gap:8px;margin:12px 0 4px}
  .range-btn{padding:5px 16px;border:1px solid rgba(0,0,0,.15);border-radius:8px;background:#f5f5f7;font-size:13px;cursor:pointer;transition:background .15s}
  .range-btn:hover{background:#e5e5ea}
  .range-btn.active{background:#0a84ff;color:#fff;border-color:#0a84ff}
  .cards{display:flex;gap:14px;margin:14px 0}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.stat{flex:1;margin-top:0}.label{font-size:12px;color:#6e6e73}.big{font-size:28px;font-weight:600;margin-top:6px}
  h2{font-size:15px;margin:0 0 10px}
  table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .spark{display:flex;align-items:flex-end;gap:3px;height:80px}
  .col{flex:1;background:#f0f0f2;border-radius:3px 3px 0 0;display:flex;align-items:flex-end;min-width:4px}
  .colfill{width:100%;background:#34c759;border-radius:3px 3px 0 0;min-height:2px}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
  .empty{color:#6e6e73}
</style>
