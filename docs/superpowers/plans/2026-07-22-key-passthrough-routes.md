# Per-Key Allowed Passthrough Routes (UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Allowed passthrough routes" row-picker to the Virtual Keys editor that reads/writes `metadata.allowed_passthrough_routes` (merged into the key's existing metadata), so an operator can grant a key access to `auth:true` pass-through routes (e.g. `/v1/audio/voices`) from the UI on open-source LiteLLM.

**Architecture:** Frontend-only. A pure helper (`passthrough.js`) converts picker rows ↔ a route list. `Keys.svelte` gains `passthroughRows` state + an `existingMetadata` snapshot, renders the picker under Allowed models, and in `buildKeyFields()` merges `allowed_passthrough_routes` into the key's preserved metadata (because `/key/update` REPLACES metadata). No backend change — `keys_routes` forwards the payload unchanged.

**Tech Stack:** Svelte 5 runes + Vite (`cd ui/frontend && npm run build`); pure JS libs sanity-checked with `node`. Backend suite (`ui/.venv/bin/python -m pytest`) only to confirm no regression. NEVER use system `python3` for backend tests.

## Global Constraints

- **Mechanism is `metadata.allowed_passthrough_routes`** (the top-level `allowed_passthrough_routes` param is Enterprise-gated → 403 on OSS; the metadata sub-key is what the OSS enforcer reads — verified live on .75/1.89.2).
- **`/key/update` REPLACES metadata** (verified) → the UI MUST read-merge-write: preserve the key's other metadata keys; never send a metadata that drops them.
- **Always send `payload.metadata`** when the key has any metadata to preserve OR any routes to set, so clearing all rows removes the sub-key. If both `existingMetadata` is empty AND routes is empty, OMIT `metadata` (don't write `{}` on a brand-new bare key).
- Match is exact-or-prefix in LiteLLM: one entry `/v1/audio/voices` also authorizes `/v1/audio/voices/combine`.
- Admin-only to set — the UI uses the master key (proxy admin) via `keys_client`, so it's permitted; no UI gating.
- This is a **route** ACL, independent of the model ACL — do NOT inject routes into allowed-models (contrast the alias-name injection).
- No JS test framework: pure helpers are verified with `node`; the build is the compile gate; behavior is proven by the Playwright integration.

---

### Task 1: `passthrough.js` pure helper (node-verified)

**Files:**
- Create: `ui/frontend/src/lib/passthrough.js`

**Interfaces:**
- Produces: `passthroughRowsToList(rows: string[]) -> string[]` (trim each, drop blanks, dedup preserving first-seen order); `listToPassthroughRows(value) -> string[]` (array of strings only → copy; anything else → `[]`).

- [ ] **Step 1: Write the helper** — create `ui/frontend/src/lib/passthrough.js`:

```js
// Per-key allowed passthrough routes: convert between picker rows (an array of
// route strings) and the wire list stored at metadata.allowed_passthrough_routes.
// LiteLLM matches these exact-or-prefix, so "/v1/audio/voices" also authorizes
// "/v1/audio/voices/combine".

export function passthroughRowsToList(rows) {
  const out = []
  for (const r of rows || []) {
    const v = (typeof r === 'string' ? r : '').trim()
    if (v && !out.includes(v)) out.push(v)   // drop blanks; dedup, first-seen order
  }
  return out
}

export function listToPassthroughRows(value) {
  if (!Array.isArray(value)) return []
  return value.filter(v => typeof v === 'string' && v.trim()).map(v => v.trim())
}
```

- [ ] **Step 2: Verify with node** — run:

```bash
cd ui/frontend && node --input-type=module -e "
import { passthroughRowsToList, listToPassthroughRows } from './src/lib/passthrough.js';
const eq = (a,b,m) => { if (JSON.stringify(a)!==JSON.stringify(b)) { console.error('FAIL',m,a,'!=',b); process.exit(1) } };
eq(passthroughRowsToList(['/a',' /b ','','/a']), ['/a','/b'], 'trim+dedup+dropblank');
eq(passthroughRowsToList([]), [], 'empty');
eq(passthroughRowsToList(null), [], 'null');
eq(listToPassthroughRows(['/a','/b']), ['/a','/b'], 'list roundtrip');
eq(listToPassthroughRows('nope'), [], 'non-array');
eq(listToPassthroughRows(undefined), [], 'undef');
console.log('passthrough.js: 6/6 OK');
"
```
Expected: `passthrough.js: 6/6 OK`.

- [ ] **Step 3: Commit**
```bash
git add ui/frontend/src/lib/passthrough.js
git commit -m "feat: passthrough.js — rows<->list helper for per-key allowed passthrough routes"
```

---

### Task 2: Wire the picker into Keys.svelte (read-merge-write metadata)

**Files:**
- Modify: `ui/frontend/src/routes/Keys.svelte`

**Interfaces:**
- Consumes: `passthroughRowsToList`, `listToPassthroughRows` (Task 1).

- [ ] **Step 1: Import the helper** — in the `<script>` import block (near the other lib imports), add:
```js
  import { passthroughRowsToList, listToPassthroughRows } from '../lib/passthrough.js'
```

- [ ] **Step 2: Add state** — after the `aliasRows` state (around line 36), add:
```js
  let passthroughRows = $state([])       // string[] — allowed passthrough routes
  let existingMetadata = $state({})      // preserved so /key/update (which REPLACES metadata) keeps other keys
  function addRoute() { passthroughRows = [...passthroughRows, ''] }
  function rmRoute(i) { passthroughRows = passthroughRows.filter((_, j) => j !== i) }
```

- [ ] **Step 3: Reset on new/clear** — in `resetFb()` (line 23), append the two resets. Change:
```js
  function resetFb() { fbMode = 'picker'; fbRules = []; fbErr = ''; form.router_fallbacks = ''; aliasRows = [] }
```
to:
```js
  function resetFb() { fbMode = 'picker'; fbRules = []; fbErr = ''; form.router_fallbacks = ''; aliasRows = []; passthroughRows = []; existingMetadata = {} }
```

- [ ] **Step 4: Build the metadata payload** — in `buildKeyFields()`, immediately BEFORE the final `Object.keys(payload).forEach(...)` cleanup line (line 97), insert:
```js
    // Allowed passthrough routes live under metadata (the top-level param is
    // Enterprise-gated; the OSS enforcer reads metadata.allowed_passthrough_routes).
    // /key/update REPLACES metadata, so merge into the key's existing metadata.
    const routes = passthroughRowsToList(passthroughRows)
    const meta = { ...existingMetadata }
    if (routes.length) meta.allowed_passthrough_routes = routes
    else delete meta.allowed_passthrough_routes            // clearing removes the sub-key
    if (Object.keys(meta).length) payload.metadata = meta  // omit metadata:{} on a bare new key
```

- [ ] **Step 5: Load on edit** — in `editKey(k)`, after the `aliasRows = aliasesToRules(k.aliases)` line (line 118), insert:
```js
    existingMetadata = { ...(k.metadata || {}) }
    passthroughRows = listToPassthroughRows(k.metadata?.allowed_passthrough_routes)
```
And extend the `showRouterSettings` visibility so an existing route also reveals the section it lives near — change line 120:
```js
    showRouterSettings = !!k.router_settings || aliasRows.length > 0
```
to:
```js
    showRouterSettings = !!k.router_settings || aliasRows.length > 0 || passthroughRows.length > 0
```
(Only if the picker is placed inside the Router Settings `<details>`; if placed in the main form per Step 6, leave line 120 unchanged. **Placement decision below: main form, so DO NOT change line 120** — this sub-step is a no-op; skip it.)

- [ ] **Step 6: Render the picker** — insert this block in the template immediately AFTER the Models `</label>` closing tag and BEFORE the `{#if editReach.length}` block (i.e. after line 169, before line 170):
```svelte
      <div class="passthrough">
        <span class="field-name">Allowed passthrough routes</span>
        {#each passthroughRows as _, i}
          <div class="pt-row">
            <input bind:value={passthroughRows[i]} placeholder="/v1/audio/voices" aria-label="passthrough route" />
            <button type="button" class="pt-x" onclick={() => rmRoute(i)} aria-label="remove route">✕</button>
          </div>
        {/each}
        <button type="button" class="pt-add" onclick={addRoute}>+ Add route</button>
        <span class="hint">Routes this key may reach on the proxy's pass-through endpoints (e.g. <code>/v1/audio/voices</code>). Prefix-matched — <code>/v1/audio/voices</code> also allows <code>/v1/audio/voices/combine</code>.</span>
      </div>
```

- [ ] **Step 7: Styles** — add to the component `<style>` (reuse existing `.field-name`/`.hint` if present; only add the new classes):
```svelte
  .passthrough{margin:8px 0}
  .pt-row{display:flex;gap:6px;align-items:center;margin:4px 0}
  .pt-row input{flex:1;min-width:0}
  .pt-x{border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer;padding:4px 9px}
  .pt-add{margin-top:4px;font-size:12px;padding:3px 12px;border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer}
```

- [ ] **Step 8: Build** — `cd ui/frontend && npm run build` → `✓ built`, no errors.

- [ ] **Step 9: Commit**
```bash
git add ui/frontend/src/routes/Keys.svelte
git commit -m "feat(ui): per-key Allowed passthrough routes picker (writes metadata, preserves existing keys)"
```

---

### Task 3: Docs, integration (end-to-end 403→200), release, deploy (controller)

**Files:**
- Modify: `docs/admin-ui-guide.md` (Virtual Keys → passthrough-routes subsection).

- [ ] **Step 1: Docs** — add under the Virtual Keys section:
```markdown
### Allowed passthrough routes

Pass-through endpoints declared with `auth: true` (see Config → passthrough) are
**deny-by-default** for virtual keys — a key gets `403 … Configure
allowed_passthrough_routes` until you grant the route here. Add each route the
key may call (e.g. `/v1/audio/voices`). Matching is **prefix-based**, so
`/v1/audio/voices` also authorizes `/v1/audio/voices/combine`.

> On open-source LiteLLM this is stored under the key's **metadata**
> (`metadata.allowed_passthrough_routes`) — the top-level `allowed_passthrough_routes`
> field is an Enterprise-only feature. The admin UI handles this for you; it also
> preserves any other metadata on the key. The master key (admin) bypasses this
> check entirely.
```
Commit: `git add docs/admin-ui-guide.md && git commit -m "docs: per-key allowed passthrough routes (Virtual Keys)"`

- [ ] **Step 2: Suite + build** — `cd ui && .venv/bin/python -m pytest tests/ -q` (expect unchanged 309/1 — no backend change) and `cd ui/frontend && npm run build`.

- [ ] **Step 3: Local hybrid stack integration (the end-to-end proof).** Recreate `docker-compose.override.yml` (litellm+UI `STORE_MODEL_IN_DB: "true"`, UI `build: ./ui`); `docker compose up -d --build llm-proxy-ui`; wait healthy. Then:
  - Stage + Apply an `auth:true` passthrough via the UI passthrough editor: `general_settings.pass_through_endpoints: [{path: /v1/audio/voices, target: http://127.0.0.1:9/v1/audio/voices, include_subpath: true, auth: true}]` (target is a dead port — we only need the AUTH gate to run before the forward; a 403 vs a 5xx/connection error distinguishes "denied" from "allowed-but-upstream-down").
  - Create a restricted virtual key (via the UI or master key). Call `GET /v1/audio/voices` with that key → **403** "Configure allowed_passthrough_routes" (baseline).
  - Drive the UI (Playwright if available, else the authed `/api/keys/update` via browser_evaluate): edit the key, add `/v1/audio/voices`, Save. Assert `/api/keys` shows `metadata.allowed_passthrough_routes: ["/v1/audio/voices"]`.
  - Re-call `GET /v1/audio/voices` with the key → **NOT 403** (a 5xx/connection error to the dead target is the PASS signal: auth passed, forward attempted). Also call `/v1/audio/voices/combine` → not 403 (prefix).
  - **Metadata preservation:** set a second metadata key on the virtual key out-of-band (master key `/key/update` with `metadata:{probe:"keep", allowed_passthrough_routes:[...]}`), then edit+Save via the UI, and assert `probe:"keep"` survives in `/api/keys`.
  - Clear the row + Save → `metadata.allowed_passthrough_routes` gone; route 403s again.
  - Clean up (delete test key, remove the passthrough); `docker compose down && rm docker-compose.override.yml`.

- [ ] **Step 4: Final review** (sonnet — small frontend diff) over the branch → fix Critical/Important → finishing-a-development-branch: merge `--no-ff` to main (CI cuts **1.31.2** via the hardened release.yml), pull the release commit, bump the UI pin, deploy to `.75` UI-only (litellm `StartedAt` unchanged). Then re-stage the Kokoro passthrough is already staged on .75 from the prior step — leave it; the operator applies. Update memory.

---

## Self-Review

**Spec coverage:** metadata mechanism not top-level param (T2 Step 4) ✓; read-merge-write preserving existing metadata (T2 Steps 2/4/5) ✓; always-send-with-omit-empty rule (T2 Step 4) ✓; clearing removes sub-key (T2 Step 4 `delete`) ✓; row-picker under Allowed models (T2 Step 6) ✓; not injected into allowed-models (no withAliasNames-style call — routes only touch metadata) ✓; pure helper node-verified (T1) ✓; end-to-end 403→200 + prefix + metadata-preservation + clear-reverts integration (T3 Step 3) ✓; docs incl. OSS/metadata note (T3 Step 1) ✓; no backend change (T3 Step 2 asserts unchanged suite) ✓.

**Placeholder scan:** none — full code in every step; T2 Step 5's conditional sub-step is explicitly resolved to a no-op (main-form placement). Integration uses a dead-port target with an explicit 403-vs-non-403 pass criterion (no live Kokoro needed).

**Type consistency:** `passthroughRowsToList`/`listToPassthroughRows` names + string[] shapes match between T1 (definition), T2 Step 1 (import) and Steps 4/5 (use); `passthroughRows` (string[]) and `existingMetadata` (object) are introduced in T2 Step 2 and consumed in Steps 4/5/6; `payload.metadata` is the wire field `keys_client` forwards to `/key/update`.
