<script>
  import { onMount } from 'svelte'
  import { PROVIDERS, buildLitellmParams } from '../lib/providers.js'
  let { store } = $props()
  let showAdd = $state(false)
  let provider = $state(PROVIDERS[0])
  let form = $state({ modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '' })
  onMount(() => { if (!store.config) store.load() })

  function models() { return store.config?.model_list ?? [] }
  function resetForm() { form = { modelName: '', modelId: '', api_key_env: '', api_base: '', api_version: '', aws_region_name: '' }; provider = PROVIDERS[0]; showAdd = false }
  async function addModel() {
    const entry = { model_name: form.modelName, litellm_params: buildLitellmParams(provider, form) }
    const ok = await store.saveSection('model_list', [...models(), entry])
    if (ok) resetForm()   // keep the user's input on a rejected save (422)
  }
  async function deleteModel(i) {
    await store.saveSection('model_list', models().filter((_, j) => j !== i))
  }
</script>

<div class="page">
  <header><h1>Models</h1>
    <button class="primary" onclick={() => showAdd = !showAdd} disabled={store.applying}>＋ Add model</button>
  </header>
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}

  {#if showAdd}
    <div class="card add">
      <label>Provider
        <select bind:value={provider}>{#each PROVIDERS as p}<option value={p}>{p.label}</option>{/each}</select>
      </label>
      <label>Public model name <input bind:value={form.modelName} placeholder="e.g. gpt-4o" /></label>
      <label>Provider model id <input bind:value={form.modelId} placeholder="e.g. gpt-4o (→ {provider.prefix}…)" /></label>
      {#if provider.fields.includes('api_key')}
        <label>API key env var <input bind:value={form.api_key_env} placeholder={provider.keyEnv || 'MY_API_KEY'} /></label>
      {/if}
      {#if provider.fields.includes('api_base')}<label>API base <input bind:value={form.api_base} placeholder="https://…" /></label>{/if}
      {#if provider.fields.includes('api_version')}<label>API version <input bind:value={form.api_version} placeholder="2024-02-15-preview" /></label>{/if}
      {#if provider.fields.includes('aws_region_name')}<label>AWS region <input bind:value={form.aws_region_name} placeholder="us-east-1" /></label>{/if}
      <div class="row">
        <button class="primary" onclick={addModel} disabled={store.applying || !form.modelName || !form.modelId}>Save &amp; apply</button>
        <button onclick={resetForm}>Cancel</button>
      </div>
      <p class="hint">Secrets are stored as <code>os.environ/VAR</code> — set the real value in <code>.env</code>. Config holds no secrets.</p>
    </div>
  {/if}

  <div class="card">
    {#if models().length === 0}<p class="empty">No models yet. Add one to start serving.</p>
    {:else}
      <table>
        <thead><tr><th>Model name</th><th>litellm model</th><th></th></tr></thead>
        <tbody>
          {#each models() as m, i}
            <tr><td>{m.model_name}</td><td><code>{m.litellm_params?.model}</code></td>
              <td><button class="danger" onclick={() => deleteModel(i)} disabled={store.applying}>Delete</button></td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:960px}
  header{display:flex;align-items:center;justify-content:space-between}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.add{display:flex;flex-direction:column;gap:10px;max-width:520px}
  label{display:flex;flex-direction:column;font-size:13px;color:#3a3a3c;gap:4px}
  input,select{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .row{display:flex;gap:8px;margin-top:4px}
  button{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}
  button.danger{color:#ff3b30;border-color:#ffd0cc}
  button:disabled{opacity:.5;cursor:default}
  .banner{padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:#6e6e73}.empty{color:#6e6e73}
</style>
