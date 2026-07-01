# Per-key Model Aliases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-key **Model aliases** editor (`{alias → real model}`) to the Virtual Keys form, exposing LiteLLM's existing per-key `aliases` capability.

**Architecture:** A pure `lib/aliases.js` converter (rows ↔ dict) + a picker in Keys.svelte's Router Settings collapsible, under Fallbacks. `payload.aliases` (top-level) flows through the existing keys passthrough to `/key/generate`|`/key/update`. No backend change.

**Tech Stack:** Svelte 5 runes (frontend); Node for the pure-module sanity check; Playwright for the UI round-trip. No Python/backend change. Frontend build: `cd ui/frontend && npm run build`.

## Global Constraints

- `aliases` is a **top-level** key field (sibling to `models`), NOT inside `router_settings`.
- The **alias name is free text**; the **target must be a real model in the key's Allowed models** — the target dropdown is sourced from `fbOptions` (Allowed models, or all models if unrestricted), so an unreachable alias can't be expressed.
- Frontend-only: `payload.aliases` passes through `keys_routes` → LiteLLM unchanged; hot-applied, no restart.
- Reuse the fallbacks picker's CSS (`.fb-rule`, `.fb-arrow`, `.fb-rm`, `.fb-add`, `.fb-actions`) and visual pattern.
- No JSON hatch, no global `model_group_alias`, aliases are 1:1.

---

### Task 1: `lib/aliases.js` — pure rows↔dict converter

**Files:**
- Create: `ui/frontend/src/lib/aliases.js`
- Test: node sanity check via a copied `.mjs` (no JS test framework in this project — same approach used for `lib/fallbacks.js`).

**Interfaces:**
- Produces:
  - `rulesToAliases(rows: {name,target}[]) -> {[name]: target}` — blanks dropped; duplicate names de-dupe (last wins).
  - `aliasesToRules(value: object|null) -> {name,target}[]` — string→string entries only; null/array/junk → `[]`.

- [ ] **Step 1: Write the module**

```js
// ui/frontend/src/lib/aliases.js
// Per-key model aliases: convert between picker rows and LiteLLM's wire map.
// Wire shape: { "<alias>": "<target-model>", ... }.  A picker row is
// { name: string, target: string }.  The alias name is free text; the target
// is a real model the key is allowed to call.

export function rulesToAliases(rows) {
  const out = {}
  for (const r of rows || []) {
    const name = (r.name || '').trim()
    const target = (r.target || '').trim()
    if (name && target) out[name] = target   // dup names: last wins (dict semantics)
  }
  return out
}

export function aliasesToRules(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value)
    .filter(([k, v]) => typeof k === 'string' && typeof v === 'string')
    .map(([name, target]) => ({ name, target }))
}
```

- [ ] **Step 2: Sanity-check the logic with Node**

```bash
cd ui/frontend
cp src/lib/aliases.js /tmp/aliases.mjs
cat > /tmp/altest.mjs <<'EOF'
import { rulesToAliases, aliasesToRules } from '/tmp/aliases.mjs'
let pass=0, fail=0
const eq=(a,b,m)=>{const A=JSON.stringify(a),B=JSON.stringify(b); A===B?pass++:(fail++,console.log('FAIL',m,'\n got',A,'\n exp',B))}
eq(rulesToAliases([{name:'gpt-4',target:'gpt-oss-20b'}]), {'gpt-4':'gpt-oss-20b'}, 'basic')
eq(rulesToAliases([{name:'',target:'x'},{name:'a',target:''}]), {}, 'blanks dropped')
eq(rulesToAliases([{name:'a',target:'x'},{name:'a',target:'y'}]), {a:'y'}, 'dup last-wins')
eq(rulesToAliases([{name:' a ',target:' x '}]), {a:'x'}, 'trimmed')
eq(aliasesToRules(null), [], 'null')
eq(aliasesToRules([]), [], 'array not object')
eq(aliasesToRules({'gpt-4':'gpt-oss-20b'}), [{name:'gpt-4',target:'gpt-oss-20b'}], 'dict→rows')
eq(aliasesToRules(rulesToAliases([{name:'a',target:'x'}])), [{name:'a',target:'x'}], 'round-trip')
console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail?1:0)
EOF
node /tmp/altest.mjs
```
Expected: `8 passed, 0 failed`

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/src/lib/aliases.js
git commit -m "feat(ui): aliases.js — per-key model-alias rows↔dict converter"
```

---

### Task 2: Wire the aliases picker into Keys.svelte

**Files:**
- Modify: `ui/frontend/src/routes/Keys.svelte` (import, state, helpers, `buildKeyFields`, `editKey`, `resetFb`, template, CSS)

**Interfaces:**
- Consumes: `rulesToAliases`, `aliasesToRules` (Task 1); the existing `fbOptions` `$derived` (Allowed models or all).

- [ ] **Step 1: Import the converter** — after the existing fallbacks import:

```javascript
  import { rulesToFallbacks, fallbacksToRules } from '../lib/fallbacks.js'
  import { rulesToAliases, aliasesToRules } from '../lib/aliases.js'
```

- [ ] **Step 2: Add state + helpers** — next to the fallbacks helpers (after `switchFbToPicker`):

```javascript
  let aliasRows = $state([])   // [{ name, target }]
  function addAlias() { aliasRows = [...aliasRows, { name: '', target: '' }] }
  function rmAlias(i) { aliasRows = aliasRows.filter((_, j) => j !== i) }
```

- [ ] **Step 3: Reset aliases with the other router state** — add one line inside `resetFb()` (it already runs on New key + Cancel):

```javascript
  function resetFb() { fbMode = 'picker'; fbRules = []; fbErr = ''; form.router_fallbacks = ''; aliasRows = [] }
```

- [ ] **Step 4: Serialize aliases into the payload (top-level)** — in `buildKeyFields()`, immediately before the final `Object.keys(payload).forEach(...)` cleanup line:

```javascript
    if (Object.keys(rs).length) payload.router_settings = rs
    const aliases = rulesToAliases(aliasRows)
    if (Object.keys(aliases).length) payload.aliases = aliases
    Object.keys(payload).forEach(k => payload[k] === undefined && delete payload[k])
    return payload
```

- [ ] **Step 5: Load aliases on edit** — in `editKey()`, after the fallbacks-load block (after `fbErr = ''`), before `editingToken = k.token`:

```javascript
    aliasRows = aliasesToRules(k.aliases)
```

- [ ] **Step 6: Add the picker markup** — in the Router Settings body, immediately after the closing `</label>` of the Fallbacks block:

```svelte
          <label>Model aliases
            {#each aliasRows as row, i}
              <div class="fb-rule">
                <input placeholder="alias name (what clients send)" bind:value={row.name} />
                <span class="fb-arrow">→</span>
                <select bind:value={row.target} aria-label="target model">
                  <option value="">— target model —</option>
                  {#each fbOptions as m}<option value={m}>{m}</option>{/each}
                </select>
                <button type="button" class="fb-rm" title="Remove" onclick={() => rmAlias(i)}>✕</button>
              </div>
            {/each}
            <div class="fb-actions"><button type="button" class="fb-add" onclick={addAlias}>+ Add alias</button></div>
            <span class="hint">Let this key request a name that maps to a real model. The <strong>name</strong> is anything clients send (e.g. <code>gpt-4</code>); the <strong>target</strong> is one of the key's <strong>Allowed models</strong>. Applied hot, no restart.</span>
          </label>
```

- [ ] **Step 7: Let the alias-name input flex like the selects** — change the `.fb-rule select` CSS rule to also cover inputs:

Find:
```css
  .fb-rule select{flex:1;min-width:0}
```
Replace with:
```css
  .fb-rule select,.fb-rule input{flex:1;min-width:0}
```

- [ ] **Step 8: Build to verify it compiles**

Run: `cd ui/frontend && npm run build`
Expected: `✓ built` with no errors.

- [ ] **Step 9: Commit**

```bash
git add ui/frontend/src/routes/Keys.svelte
git commit -m "feat(ui): per-key Model aliases picker in Virtual Keys Router Settings"
```

---

### Task 3: Document the feature

**Files:**
- Modify: `docs/admin-ui-guide.md` (Virtual Keys → Router Settings area, next to the Fallbacks-picker subsection)

- [ ] **Step 1: Add a "Model aliases" subsection** after the "Fallbacks picker" subsection. Use this content:

```markdown
#### Model aliases

A key can carry **model aliases** — a map of `alias name → real model`. A client
using the key can request the alias name and LiteLLM transparently routes to the
real model. Useful for handing a client app a stable, familiar name (e.g.
`gpt-4`) that you can repoint to any of your deployments without the client
changing anything.

- The **alias name** is free text — whatever clients will send. It need not be a
  real model.
- The **target** is picked from the key's **Allowed models** (the picker sources
  the dropdown from them), so an alias can only point at a model the key may call
  — access is enforced on the *resolved target*, not the alias name.
- Add a row with **+ Add alias**; remove with ✕. Applied hot via `/key/update`
  (no restart).

**Worked example:** a key allowed `gpt-oss-20b`, with an alias `gpt-4` →
`gpt-oss-20b`. A client sending `model: "gpt-4"` on that key is served by
`gpt-oss-20b`.

Not to be confused with **key alias** (the human label for the key itself, in the
Alias field) or the global `model_group_alias` (a proxy-wide alias in
`router_settings`).
```

- [ ] **Step 2: Commit**

```bash
git add docs/admin-ui-guide.md
git commit -m "docs: document the per-key Model aliases picker"
```

---

### Task 4: Integration, release, deploy

**Files:** none (verification + release).

- [ ] **Step 1: Frontend build (final)**

Run: `cd ui/frontend && npm run build` → expect clean.

- [ ] **Step 2: Bring up the local hybrid stack** (build UI from source)

```bash
cd /home/kumar/workspace/litellm
cat > docker-compose.override.yml <<'EOF'
services:
  litellm:
    environment: { STORE_MODEL_IN_DB: "true" }
  llm-proxy-ui:
    build: ./ui
    image: llm-proxy-ui:dev
    environment: { STORE_MODEL_IN_DB: "true" }
EOF
docker compose up -d --build llm-proxy-ui
# wait for litellm-ui healthy
```
Ensure ≥2 models exist in ui_config (add a 2nd via the authed API + Apply if needed, as done for fallbacks verification).

- [ ] **Step 3: Playwright — create with an alias, verify wire shape + edit round-trip**

Drive http://10.0.20.85:8081 → Virtual Keys → New key → (optionally set Allowed models) → Router Settings → **+ Add alias** → name `gpt-4`, target a real model → Create. Then:
- `fetch('/api/keys')` → assert the new key's `aliases` == `{ "gpt-4": "<target>" }`.
- Click **Edit** on it → confirm the alias row reloads (name `gpt-4`, target selected). **This also verifies `/key/update` accepts `aliases`** — save an edit and re-read to confirm it persists.

- [ ] **Step 4: Tear down local stack**

```bash
docker compose down && rm docker-compose.override.yml
```

- [ ] **Step 5: Final review + merge + release + deploy**

Dispatch the final whole-branch review (opus) on the branch diff. Then use superpowers:finishing-a-development-branch → merge to main (CI cuts the release) → bump the docker-compose pin → deploy to .75 UI-only (verify litellm `StartedAt` unchanged) → confirm the new JS bundle serves.

---

## Self-Review

**Spec coverage:** `aliases.js` converter (Task 1) ✓; Keys.svelte state/serialize(top-level)/load/reset/template/CSS (Task 2) ✓; free-text-name / allowed-target via `fbOptions` (Task 2 Step 6) ✓; docs subsection (Task 3) ✓; node + Playwright tests incl. `/key/update` check (Tasks 1, 4) ✓; out-of-scope items (global alias, multi-target, JSON hatch) not implemented ✓.

**Placeholder scan:** every code step has real code; no TBD/TODO.

**Type consistency:** `rulesToAliases`/`aliasesToRules` signatures identical across Tasks 1–2; `aliasRows` shape `{name,target}` consistent in state, helpers, serialize, load, and template; `payload.aliases` (top-level) consistent between Task 2 and the passthrough backend.
