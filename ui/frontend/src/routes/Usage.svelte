<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let d = $state(null); let err = $state(''); let loading = $state(true)
  onMount(async () => { try { d = await api.usage() } catch (e) { err = e.message } finally { loading = false } })
  const money = (n) => `$${Number(n ?? 0).toFixed(2)}`
  function maxModel() { return Math.max(1, ...((d?.by_model ?? []).map(m => m.total_spend ?? 0))) }
  function maxReq() { return Math.max(1, ...((d?.activity?.daily_data ?? []).map(x => x.api_requests ?? 0))) }
</script>

<div class="page">
  <h1>Usage &amp; Spend <span class="sub">last 30 days</span></h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if loading}<p class="empty">Loading…</p>
  {:else if d}
    <div class="cards">
      <div class="card stat"><div class="label">Total spend</div><div class="big">{money(d.total?.spend)}</div></div>
      <div class="card stat"><div class="label">Requests (30d)</div><div class="big">{d.activity?.sum_api_requests ?? 0}</div></div>
      <div class="card stat"><div class="label">Tokens (30d)</div><div class="big">{(d.activity?.sum_total_tokens ?? 0).toLocaleString()}</div></div>
    </div>

    <div class="card">
      <h2>Spend by model</h2>
      {#if (d.by_model ?? []).length === 0}<p class="empty">No spend recorded yet.</p>
      {:else}{#each d.by_model as m}
        <div class="bar-row"><span class="bk">{m.model}</span>
          <div class="bar"><div class="fill" style="width:{Math.round((m.total_spend ?? 0)/maxModel()*100)}%"></div></div>
          <span class="bv">{money(m.total_spend)}</span></div>
      {/each}{/if}
    </div>

    <div class="card">
      <h2>Spend by key</h2>
      {#if (d.by_key ?? []).length === 0}<p class="empty">No key spend yet.</p>
      {:else}<table><thead><tr><th>Key</th><th>Spend</th></tr></thead><tbody>
        {#each d.by_key as k}<tr><td>{k.key_alias || k.key_name || '—'}</td><td>{money(k.total_spend)}</td></tr>{/each}
      </tbody></table>{/if}
    </div>

    <div class="card">
      <h2>Daily requests</h2>
      {#if (d.activity?.daily_data ?? []).length === 0}<p class="empty">No activity in this window.</p>
      {:else}<div class="spark">{#each d.activity.daily_data as x}
        <div class="col" title="{x.date}: {x.api_requests} req"><div class="colfill" style="height:{Math.round((x.api_requests ?? 0)/maxReq()*100)}%"></div></div>
      {/each}</div>{/if}
    </div>
  {/if}
</div>

<style>
  .page{padding:24px 30px;max-width:960px}.sub{font-size:13px;color:#6e6e73;font-weight:400}
  .cards{display:flex;gap:14px;margin:14px 0}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.stat{flex:1;margin-top:0}.label{font-size:12px;color:#6e6e73}.big{font-size:28px;font-weight:600;margin-top:6px}
  h2{font-size:15px;margin:0 0 10px}
  .bar-row{display:grid;grid-template-columns:160px 1fr 70px;align-items:center;gap:10px;margin:6px 0;font-size:13px}
  .bar{background:#f0f0f2;border-radius:6px;height:14px;overflow:hidden}.fill{height:100%;background:#0a84ff}
  .bk{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bv{text-align:right;color:#3a3a3c}
  table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .spark{display:flex;align-items:flex-end;gap:3px;height:80px}
  .col{flex:1;background:#f0f0f2;border-radius:3px 3px 0 0;display:flex;align-items:flex-end;min-width:4px}
  .colfill{width:100%;background:#34c759;border-radius:3px 3px 0 0;min-height:2px}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}.empty{color:#6e6e73}
</style>
