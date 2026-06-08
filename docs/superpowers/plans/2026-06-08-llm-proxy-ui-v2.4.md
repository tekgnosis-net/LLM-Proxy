# LLM-Proxy Admin UI — v2.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD. Frontend (Svelte 5) = build + real-stack verification. Steps use `- [ ]`. **Builds on v2.1–v2.3.**

**Goal:** Ship the Provider-Keys save fix (done), add a **Discard staged changes** escape hatch, and make the Models **provider picker catalog-driven** (full LiteLLM provider list + per-provider supported modes; `api_base` hidden behind an advanced toggle since LiteLLM resolves endpoints by prefix).

**Architecture:** Discard = `POST /api/discard` → `restore_baseline()` (revert `config.yaml` to `.applied.yaml`, no restart). Provider picker = the already-synced `ui_provider_endpoints` catalog drives a searchable dropdown; the slug is the `provider/` prefix; modes are derived from the provider's endpoint matrix; a small curated map covers the few special deployment fields.

**Tech Stack:** FastAPI, Svelte 5. No new deps.

**Spec:** [`../specs/2026-06-08-llm-proxy-ui-v2.4-design.md`](../specs/2026-06-08-llm-proxy-ui-v2.4-design.md).

**Note — Item 1 (read-only fix) is already done** (commit `0a652ec`, unpushed): `ProviderKeys.svelte add()` no longer assigns the getter-only `store.pending`. It ships as part of this branch. No task needed; verified in-browser already.

---

## File Structure
```
ui/app/routes/config_routes.py     # MODIFY: + POST /api/discard
ui/app/catalog.py                  # MODIFY: + endpoints_to_modes() (pure)
ui/app/routes/catalog_routes.py    # MODIFY: /api/catalog/providers attaches modes
ui/tests/test_config_routes.py     # MODIFY: discard tests
ui/tests/test_catalog.py           # MODIFY: endpoints_to_modes tests
ui/tests/test_catalog_routes.py    # MODIFY: providers-include-modes test
ui/frontend/src/lib/api.js         # MODIFY: + discard()
ui/frontend/src/lib/configStore.svelte.js  # MODIFY: + discard()
ui/frontend/src/App.svelte         # MODIFY: Discard button in Apply bar
ui/frontend/src/lib/providers.js   # MODIFY: static fallback list + special-field map + reshaped buildLitellmParams
ui/frontend/src/routes/Models.svelte       # MODIFY: catalog provider dropdown + mode filter + advanced api_base + special fields
ui/frontend/src/routes/ProviderKeys.svelte # MODIFY: provider dropdown now uses the new provider list (was PROVIDERS)
```

> **Consumers of the old `PROVIDERS` export:** BOTH `Models.svelte` AND `ProviderKeys.svelte`
> import it. Reshaping `providers.js` (Task 4) breaks both builds until both are updated —
> so Tasks 4 + 5 land in **one commit** and Task 5 updates ProviderKeys too.

---

## Task 1: backend — `POST /api/discard` (TDD)

**Files:** Modify `ui/app/routes/config_routes.py`, `ui/tests/test_config_routes.py`.

- [ ] **Step 1: failing tests** (append to `test_config_routes.py`; reuse `_client`):
```python
def test_discard_clears_pending(tmp_path):
    c = _client(tmp_path)
    # seed baseline, then stage a change
    c.get("/api/apply/status")  # seeds .applied.yaml from current config
    c.put("/api/config", json={"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    assert c.get("/api/apply/status").json()["pending"] is True
    r = c.post("/api/discard")
    assert r.status_code == 200 and r.json()["pending"] is False
    # config.yaml reverted: routing back to the baseline value
    from app.config_store import load_config
    import os
    assert load_config(os.environ["CONFIG_PATH"]).router_settings.routing_strategy == "least-busy" or True  # baseline was least-busy? see note


def test_discard_requires_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.post("/api/discard").status_code == 401


def test_discard_no_baseline_is_noop(tmp_path):
    c = _client(tmp_path)
    # no apply/status called first → restore is a no-op; discard still returns a clean status
    r = c.post("/api/discard")
    assert r.status_code == 200 and "pending" in r.json()
```
NOTE on the revert assertion: the `_client` helper writes `config.yaml` with `routing_strategy: least-busy` initially. The baseline is seeded from that. After staging `least-busy` again (same) the diff may be empty — to make the test meaningful, change the staged value to something different from the seeded file. Adjust the test to: seed (status), `PUT` a *different* strategy (`simple-shuffle`), assert pending true, discard, assert pending false AND `load_config(...).router_settings.routing_strategy == "least-busy"` (the seeded baseline value). Write the test with concrete distinct values so the revert is observable.

- [ ] **Step 2: run red** — `cd ui && .venv/bin/python -m pytest tests/test_config_routes.py -k discard -v` → FAIL (404, route missing).

- [ ] **Step 3: implement** in `config_routes.py` (imports: `restore_baseline`, `pending_status`, `seed_baseline_if_missing` from config_store are/should be imported):
```python
@router.post("/discard", dependencies=[Depends(login_required)])
def discard():
    s = get_settings()
    seed_baseline_if_missing(s.config_path)   # ensure a baseline exists (first-run no-op safety)
    restore_baseline(s.config_path)           # copy .applied.yaml -> config.yaml (no proxy restart needed)
    return pending_status(s.config_path)
```
(Ensure `restore_baseline` and `seed_baseline_if_missing` are in the import line from `app.config_store`.)

- [ ] **Step 4: run green + full suite** — `cd ui && .venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 5: commit**
```bash
git add ui/app/routes/config_routes.py ui/tests/test_config_routes.py
git commit -m "feat(ui): POST /api/discard — revert staged changes to last-applied baseline"
```

---

## Task 2: frontend — Discard in store + Apply bar

**Files:** Modify `ui/frontend/src/lib/api.js`, `ui/frontend/src/lib/configStore.svelte.js`, `ui/frontend/src/App.svelte`.

- [ ] **Step 1: api.js** — add to the `api` object:
```javascript
  discard: () => req('/api/discard', { method: 'POST' }),
```

- [ ] **Step 2: configStore.svelte.js** — add a `discard()` method and expose it. Insert after `apply()`:
```javascript
  async function discard() {
    saving = true; error = ''; notice = ''
    try {
      await api.discard()
      config = await api.config()        // re-pull the reverted config so open screens reflect it
      await refreshPending()
      notice = 'Discarded unapplied changes — reverted to the last applied config.'
      return true
    } catch (e) { error = e.message; await refreshPending(); return false }
    finally { saving = false }
  }
```
Add `discard,` to the returned object (next to `apply,`).

- [ ] **Step 3: App.svelte** — in the Apply bar (shown when `store.pending`), add a **Discard** button before/after Apply. Replace the bar's button area so it has both:
```svelte
      <div class="applybar-actions">
        <button class="discard" onclick={confirmDiscard} disabled={store.saving || store.applying}>Discard</button>
        <button class="apply" onclick={() => store.apply()} disabled={store.applying || store.saving}>{store.applying ? 'Applying… (~25s)' : 'Apply'}</button>
      </div>
```
In the `<script>`, add:
```javascript
  function confirmDiscard() {
    const n = store.pendingSummary.length
    if (confirm(`Discard ${n} unapplied change${n === 1 ? '' : 's'}? This reverts config.yaml to the last applied state.`)) {
      store.discard()
    }
  }
```
Add styles:
```css
  .applybar-actions{display:flex;gap:8px;align-items:center}
  .applybar .discard{background:transparent;color:#7a5b00;border:1px solid #e0c074;border-radius:8px;padding:6px 12px;font-weight:600;cursor:pointer}
  .applybar .discard:disabled{opacity:.5}
```
(Keep the existing `.apply` button styles.)

- [ ] **Step 4: build** — `cd ui/frontend && npm run build` → success.

- [ ] **Step 5: commit**
```bash
git add ui/frontend/src/lib/api.js ui/frontend/src/lib/configStore.svelte.js ui/frontend/src/App.svelte
git commit -m "feat(ui): Discard button — revert staged changes (no restart)"
```

---

## Task 3: backend — derive `modes` for catalog providers (TDD)

**Files:** Modify `ui/app/catalog.py`, `ui/app/routes/catalog_routes.py`, `ui/tests/test_catalog.py`, `ui/tests/test_catalog_routes.py`.

- [ ] **Step 1: failing test** (append to `test_catalog.py`):
```python
from app.catalog import endpoints_to_modes

def test_endpoints_to_modes_maps_supported_only():
    eps = {"chat_completions": True, "embeddings": True, "rerank": False, "image_generation": True}
    modes = endpoints_to_modes(eps)
    assert "chat" in modes and "embedding" in modes and "image_generation" in modes
    assert "rerank" not in modes

def test_endpoints_to_modes_accepts_json_string():
    import json
    assert "chat" in endpoints_to_modes(json.dumps({"chat_completions": True}))

def test_endpoints_to_modes_empty():
    assert endpoints_to_modes(None) == [] and endpoints_to_modes({}) == []
```

- [ ] **Step 2: run red** → FAIL (`endpoints_to_modes` missing).

- [ ] **Step 3: implement** in `catalog.py` (module level):
```python
import json as _json

_ENDPOINT_MODE = {
    "chat_completions": "chat", "completion": "completion", "embeddings": "embedding",
    "rerank": "rerank", "image_generation": "image_generation",
    "audio_transcription": "audio_transcription", "audio_speech": "audio_speech",
    "moderations": "moderations", "moderation": "moderations", "responses": "responses",
}

def endpoints_to_modes(endpoints) -> list[str]:
    """Map a provider's endpoint-support map to litellm `mode` values (supported only).
    Accepts a dict or a JSON string (asyncpg returns jsonb as text)."""
    if isinstance(endpoints, str):
        try: endpoints = _json.loads(endpoints)
        except Exception: return []
    out = []
    for k, v in (endpoints or {}).items():
        m = _ENDPOINT_MODE.get(k)
        if v and m and m not in out:
            out.append(m)
    return out
```

- [ ] **Step 4: route test** (append to `test_catalog_routes.py`): extend `FakeCatalog.get_providers` to include an `endpoints` map, and assert the route attaches `modes`:
```python
def test_providers_include_modes(tmp_path):
    class ModeCat(FakeCatalog):
        async def get_providers(self): return [{"provider":"openai","display_name":"OpenAI","endpoints":{"chat_completions":True,"embeddings":True}}]
    r = _client(tmp_path, ModeCat()).get("/api/catalog/providers")
    p = r.json()[0]
    assert set(["chat","embedding"]).issubset(set(p["modes"]))
```

- [ ] **Step 5: implement route change** in `catalog_routes.py` — attach modes to each provider:
```python
from app.catalog import endpoints_to_modes
# in catalog_providers():
    rows = await make_catalog().get_providers()
    for r in rows:
        r["modes"] = endpoints_to_modes(r.get("endpoints"))
    return rows
```
(Wrap with the existing try/except → 502 pattern.)

- [ ] **Step 6: green + full suite.** **Step 7: commit** `feat(ui): catalog providers expose supported modes (endpoints→modes)`.

---

## Task 4: frontend — providers.js (static fallback + special-field map + reshaped builder)

**Files:** Modify `ui/frontend/src/lib/providers.js`.

- [ ] **Step 1: rewrite `providers.js`** to support catalog-driven providers + a cold-start fallback + the curated special-field map. Full new file:
```javascript
// Catalog-driven providers. The live list comes from /api/catalog/providers
// (synced provider_endpoints_support.json). This static list is the COLD-START
// fallback only (before first catalog sync / offline) — a snapshot of LiteLLM's
// common chat providers. The slug is the litellm `provider/` prefix.
export const FALLBACK_PROVIDERS = [
  { provider: 'openai', display_name: 'OpenAI' },
  { provider: 'anthropic', display_name: 'Anthropic' },
  { provider: 'azure', display_name: 'Azure OpenAI' },
  { provider: 'gemini', display_name: 'Google Gemini' },
  { provider: 'vertex_ai', display_name: 'Google Vertex AI' },
  { provider: 'bedrock', display_name: 'AWS Bedrock' },
  { provider: 'cohere', display_name: 'Cohere' },
  { provider: 'mistral', display_name: 'Mistral' },
  { provider: 'groq', display_name: 'Groq' },
  { provider: 'deepseek', display_name: 'DeepSeek' },
  { provider: 'xai', display_name: 'xAI' },
  { provider: 'openrouter', display_name: 'OpenRouter' },
  { provider: 'together_ai', display_name: 'Together AI' },
  { provider: 'fireworks_ai', display_name: 'Fireworks AI' },
  { provider: 'perplexity', display_name: 'Perplexity' },
  { provider: 'ollama', display_name: 'Ollama (local)' },
  { provider: 'hosted_vllm', display_name: 'vLLM (hosted)' },
  { provider: 'openai_compatible', display_name: 'OpenAI-compatible / custom' },
]

// Common providers pinned to the top of the picker.
export const PINNED_PROVIDERS = ['openai', 'anthropic', 'azure', 'bedrock', 'gemini', 'vertex_ai']

// Full mode list (fallback when a provider has no catalog modes).
export const ALL_MODES = ['chat','embedding','completion','image_generation','audio_transcription','audio_speech','rerank','moderations','responses']

// Special deployment fields LiteLLM doesn't expose as data — shown only for these slugs.
export const SPECIAL_PROVIDER_FIELDS = {
  azure: ['api_base', 'api_version'],
  bedrock: ['aws_region_name'],
  vertex_ai: ['vertex_project', 'vertex_location'],
}

// Build litellm_params from the chosen provider slug + form. Secrets are emitted as
// os.environ/<VAR> only (config holds no literal secrets; credentials use the vault).
export function buildLitellmParams(slug, form) {
  const p = { model: `${slug}/${form.modelId}` }
  if (form.api_base) p.api_base = form.api_base
  if (form.api_version) p.api_version = form.api_version
  if (form.aws_region_name) p.aws_region_name = form.aws_region_name
  if (form.vertex_project) p.vertex_project = form.vertex_project
  if (form.vertex_location) p.vertex_location = form.vertex_location
  // api_key env-var path (only when no saved credential is selected)
  if (!form.credential && form.api_key_env) p.api_key = `os.environ/${form.api_key_env}`
  return p
}
```

- [ ] **Step 2: build** — `cd ui/frontend && npm run build` → success (Models.svelte still imports `PROVIDERS`/`buildLitellmParams`; it will break the build until Task 5 updates it. To keep this task's build green, do Task 4 + Task 5 together OR temporarily keep a `PROVIDERS` export). **To avoid a broken intermediate build, COMBINE Task 4 and Task 5 into one commit** (build once at the end of Task 5). Skip the standalone build here.

- [ ] **Step 3:** (no commit yet — committed with Task 5.)

---

## Task 5: frontend — Models.svelte catalog provider picker

**Files:** Modify `ui/frontend/src/routes/Models.svelte`, `ui/frontend/src/lib/api.js` (already has `catalogProviders`).

- [ ] **Step 1: update imports + state** in `Models.svelte`:
```javascript
  import { FALLBACK_PROVIDERS, PINNED_PROVIDERS, ALL_MODES, SPECIAL_PROVIDER_FIELDS, buildLitellmParams } from '../lib/providers.js'
  // replace `provider = $state(PROVIDERS[0])` with:
  let providers = $state(FALLBACK_PROVIDERS)     // catalog list (or fallback)
  let providerSlug = $state('openai')
  let showAdvanced = $state(false)               // reveals api_base for custom/self-hosted
  // form keeps: modelName, modelId, api_key_env, api_base, api_version, aws_region_name,
  //   vertex_project, vertex_location, credential, mode, input_cost, output_cost
```
Add `vertex_project: '', vertex_location: ''` to the `form` initial object and to `resetForm()`.

- [ ] **Step 2: load providers** — in `onMount`, after loading credentials, fetch the catalog providers (fallback on error):
```javascript
    try {
      const ps = await api.catalogProviders()
      if (Array.isArray(ps) && ps.length) {
        const pinned = PINNED_PROVIDERS.map(s => ps.find(p => p.provider === s)).filter(Boolean)
        const rest = ps.filter(p => !PINNED_PROVIDERS.includes(p.provider)).sort((a,b)=> (a.display_name||a.provider).localeCompare(b.display_name||b.provider))
        providers = [...pinned, ...rest]
      }
    } catch (_) { providers = FALLBACK_PROVIDERS }
```

- [ ] **Step 3: derived helpers** (add to script):
```javascript
  function currentProvider() { return providers.find(p => p.provider === providerSlug) || { provider: providerSlug } }
  function providerModes() {
    const m = currentProvider().modes
    return (Array.isArray(m) && m.length) ? m : ALL_MODES
  }
  function specialFields() { return SPECIAL_PROVIDER_FIELDS[providerSlug] || [] }
  function onProviderChange() {
    testResult = null; autofilled = false
    const modes = providerModes()
    if (!modes.includes(form.mode)) form.mode = modes[0] || 'chat'
  }
```
Update `buildParams()` to use the slug:
```javascript
  function buildParams() {
    const lp = buildLitellmParams(providerSlug, form)
    if (form.credential) { delete lp.api_key; lp.litellm_credential_name = form.credential }
    if (form.input_cost !== '' && form.input_cost !== null) lp.input_cost_per_token = Number(form.input_cost)
    if (form.output_cost !== '' && form.output_cost !== null) lp.output_cost_per_token = Number(form.output_cost)
    return lp
  }
```
Update `tryAutofill()` to build the full name from the slug: `const full = providerSlug + '/' + form.modelId`.

- [ ] **Step 4: markup** — replace the Provider `<select>` block and the field section. Provider picker (searchable via datalist):
```svelte
      <label>Provider
        <input list="provider-list" bind:value={providerSlug} onchange={onProviderChange} placeholder="search providers…" />
        <datalist id="provider-list">
          {#each providers as p}<option value={p.provider}>{p.display_name || p.provider}</option>{/each}
        </datalist>
      </label>
      <label>Public model name <input bind:value={form.modelName} placeholder="e.g. gpt-4o" /></label>
      <label>Provider model id
        <div class="lookup-row">
          <span class="prefix">{providerSlug}/</span>
          <input bind:value={form.modelId} placeholder="e.g. gpt-4o" onblur={tryAutofill} />
          <button type="button" onclick={tryAutofill} disabled={autofillBusy || !form.modelId}>{autofillBusy ? '…' : 'Look up pricing'}</button>
        </div>
        {#if autofilled}<span class="autofill-hint">auto-filled from catalog</span>{/if}
      </label>

      <label>Credential
        <select bind:value={form.credential}>
          <option value="">— env var / none —</option>
          {#each credentials as c}<option value={c.credential_name}>{c.credential_name}</option>{/each}
        </select>
      </label>
      {#if !form.credential}
        <label>API key env var <input bind:value={form.api_key_env} placeholder="e.g. OPENAI_API_KEY" /></label>
      {/if}

      <!-- Special per-provider deployment fields (curated) -->
      {#if specialFields().includes('api_version')}<label>API version <input bind:value={form.api_version} placeholder="2024-02-15-preview" /></label>{/if}
      {#if specialFields().includes('aws_region_name')}<label>AWS region <input bind:value={form.aws_region_name} placeholder="us-east-1" /></label>{/if}
      {#if specialFields().includes('vertex_project')}<label>Vertex project <input bind:value={form.vertex_project} placeholder="my-gcp-project" /></label>{/if}
      {#if specialFields().includes('vertex_location')}<label>Vertex location <input bind:value={form.vertex_location} placeholder="us-central1" /></label>{/if}

      <label>Mode
        <select bind:value={form.mode}>{#each providerModes() as m}<option value={m}>{m}</option>{/each}</select>
      </label>

      <!-- Advanced: custom endpoint (LiteLLM resolves the URL from the prefix otherwise) -->
      <button type="button" class="link" onclick={() => showAdvanced = !showAdvanced}>{showAdvanced ? '▾' : '▸'} Advanced: custom endpoint</button>
      {#if showAdvanced || specialFields().includes('api_base')}
        <label>API base (override / self-hosted) <input bind:value={form.api_base} placeholder="https://your-endpoint/v1 — leave blank to let LiteLLM resolve" /></label>
      {/if}

      <label>Input cost/token <input type="number" step="1e-9" min="0" bind:value={form.input_cost} placeholder="auto from catalog" /></label>
      <label>Output cost/token <input type="number" step="1e-9" min="0" bind:value={form.output_cost} placeholder="auto from catalog" /></label>
```
Keep the existing Test/Save/Cancel button row + testResult banner + the hint paragraph. Update the hint to mention LiteLLM resolves the endpoint from the provider.

- [ ] **Step 5: styles** — add:
```css
  .prefix{display:inline-flex;align-items:center;padding:0 8px;background:#f0f0f3;border:1px solid #ccc;border-right:0;border-radius:8px 0 0 8px;font:inherit;color:#6e6e73;white-space:nowrap}
  .lookup-row .prefix + input{border-radius:0}
  button.link{background:none;border:0;color:#0a84ff;cursor:pointer;font-size:12px;padding:0;text-align:left;width:fit-content}
```

- [ ] **Step 6: update `ProviderKeys.svelte`** (it imported the removed `PROVIDERS`). Replace its provider dropdown to use the same catalog/fallback list. In the `<script>`: `import { FALLBACK_PROVIDERS } from '../lib/providers.js'` and `import { api } from '../lib/api.js'` (already imported); add `let providers = $state(FALLBACK_PROVIDERS)`; in `onMount`/`load`, try `const ps = await api.catalogProviders(); if (Array.isArray(ps) && ps.length) providers = ps` (fallback kept on error). Change the markup option loop from `{#each PROVIDERS as p}<option value={p.id}>{p.label}</option>` to:
```svelte
    <label>Provider <select bind:value={form.provider}>{#each providers as p}<option value={p.provider}>{p.display_name || p.provider}</option>{/each}</select></label>
```
(The credential's `provider` is stored as the slug, which is what we want.)

- [ ] **Step 7: build** — `cd ui/frontend && npm run build` → success (resolves Task 4 + 5 imports across BOTH Models.svelte and ProviderKeys.svelte).

- [ ] **Step 8: commit**
```bash
git add ui/frontend/src/lib/providers.js ui/frontend/src/routes/Models.svelte ui/frontend/src/routes/ProviderKeys.svelte
git commit -m "feat(ui): catalog-driven provider picker (full list, modes filter, advanced api_base)"
```

---

## Task 6: real-stack integration verification

**Files:** none (verification). Use a local-build override (as in v2 integration tests).

- [ ] **Step 1:** `cd /home/kumar/workspace/litellm`; reset config (`rm -f config/config.yaml config/.applied.yaml config/*.bak.*; cp config/config.yaml.example config/config.yaml`); `printf 'services:\n  llm-proxy-ui:\n    build: ./ui\n' > docker-compose.override.yml`; `docker compose up -d --build --wait`. Login (`.env` admin pw).
- [ ] **Step 2 — the user's exact sequence (read-only fix):** via Playwright, Provider Keys → Add key → fill → **Save** → assert **no error banner**, key appears in list, Apply bar shows "1 unapplied change (credential_list)". (Confirms Item 1.)
- [ ] **Step 3 — Discard:** click **Discard** → confirm → Apply bar clears (pending=false); `GET /api/apply/status` → false; `config.yaml` no longer has the credential_list. (Confirms Item 2; no restart — proxy `StartedAt` unchanged.)
- [ ] **Step 4 — provider picker:** Models → Add model → the Provider field lists catalog providers (search "groq" works); select `groq` → the model-id prefix shows `groq/`, Mode is filtered to groq's supported modes, `api_base` is hidden (Advanced collapsed); select `azure` → API version field appears; select `bedrock` → AWS region appears. Enter a model + Save → stages `model_list`; verify the saved entry's `litellm_params.model` is `groq/<id>` with no `api_base`.
- [ ] **Step 5 — apply works:** Apply → `/v1/models` lists the new model (LiteLLM resolved the endpoint from the prefix). Tear down (`docker compose down; rm docker-compose.override.yml`); restore config; `git status` clean.

---

## Self-Review
- **Spec coverage:** Item 1 read-only fix (done, noted in header) ✓; Item 2 Discard backend (T1) + frontend (T2) ✓; Item 3 modes derivation (T3) + provider picker with fallback/pinned/search (T4,T5) + advanced api_base + special fields (T5) ✓; provider list essential + cold-start fallback (T4) ✓; integration incl. the user's sequence (T6) ✓.
- **Placeholders:** Task 1's test note explicitly says to use distinct seeded-vs-staged values for an observable revert; Task 4/5 are combined to avoid a broken intermediate build. No TBDs.
- **Type consistency:** `restore_baseline`/`seed_baseline_if_missing`/`pending_status` (config_store), `endpoints_to_modes` (catalog), `api.discard`, store `discard`, `buildLitellmParams(slug, form)`, `FALLBACK_PROVIDERS`/`PINNED_PROVIDERS`/`ALL_MODES`/`SPECIAL_PROVIDER_FIELDS`, `providerSlug`/`providerModes()`/`specialFields()` consistent across tasks.

## Notes
- Task 4 removes the old `PROVIDERS` export; its TWO consumers (`Models.svelte` and
  `ProviderKeys.svelte`) are both updated in Task 5's single commit, so the build is only
  green at the end of Task 5. (Verified by grep: those are the only two importers.)
- The read-only fix (`0a652ec`) is already committed on this branch; it ships with v2.4. Nothing has been pushed yet (per the user's hold).
