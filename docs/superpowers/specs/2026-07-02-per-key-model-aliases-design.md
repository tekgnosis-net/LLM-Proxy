# Per-key Model Aliases Design

**Status:** Approved (design), 2026-07-02
**Builds on:** the per-key fallbacks picker (1.26.0) — same UI pattern and access model.

## Goal

Let a virtual key define **model aliases** — a `{alias-name → real-model}` map — through the admin UI, so a client using that key can request the alias name and LiteLLM transparently routes it to the real model. Exposes LiteLLM's existing per-key `aliases` capability (currently only reachable via the raw API).

## Motivation

LiteLLM's `/key/generate` (and `/key/update`) accept an `aliases` param:
`aliases={"gpt-4": "gpt-oss-20b"}`. A key holder's app hardcoded to `model: "gpt-4"`
then transparently hits your `gpt-oss-20b` deployment — useful for handing out a
stable, familiar name you can repoint without the client changing anything. Our
Keys form has `key_alias` (a label for the key) and `models` (allowed models) but
no per-key `aliases` editor, so today it's only settable by curling the API.

## Key semantics (the design's central asymmetry)

- An alias entry is `{ "<alias-name>": "<target-model>" }`.
- The **alias name is free text** — it's whatever clients send (`gpt-4`, `default`, …); it need not be a real model.
- The **target is a real public model name** from the Models screen.
- **Access is enforced on the resolved target, not the alias.** So the target must be in the key's Allowed models; the alias name is unconstrained. (Contrast fallbacks, where *both* ends are real models.)
- `aliases` is a **top-level** key field (sibling to `models`), NOT part of `router_settings`.

## Global Constraints

- Frontend-only feature over the existing keys passthrough: `payload.aliases` flows through `keys_routes` → `/key/generate`|`/key/update` unchanged. No backend change.
- Applies hot via `/key/update` (no restart), like all key edits.
- The target dropdown is sourced from the key's Allowed models (or all models if the key is unrestricted), so an alias to a non-callable model cannot be expressed.
- Match the fallbacks picker's visual pattern and CSS (`.fb-rule`, `.fb-arrow`, `.fb-rm`, `.fb-add`). Placed in the "Router Settings (optional)" collapsible, directly under Fallbacks.

## Components

### 1. `ui/frontend/src/lib/aliases.js` — pure conversion (mirrors `fallbacks.js`)

```js
// Per-key model aliases: convert between picker rows and LiteLLM's wire map.
// Wire shape: { "<alias>": "<target-model>", ... }.  A picker row is
// { name: string, target: string }.

export function rulesToAliases(rows) {
  // Blanks dropped; duplicate names de-dupe (last wins, dict semantics).
  const out = {}
  for (const r of rows || []) {
    const name = (r.name || '').trim()
    const target = (r.target || '').trim()
    if (name && target) out[name] = target
  }
  return out
}

export function aliasesToRules(value) {
  // value: the aliases dict (or null/undefined). Only string→string entries.
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value)
    .filter(([k, v]) => typeof k === 'string' && typeof v === 'string')
    .map(([name, target]) => ({ name, target }))
}
```

No "representable" flag and no JSON hatch — a flat string→string map always round-trips through the picker.

### 2. `ui/frontend/src/routes/Keys.svelte`

- State: `let aliasRows = $state([])` (`[{name, target}]`).
- Options: reuse the existing `fbOptions` `$derived` (key's Allowed models, or `availableModels` if unrestricted) for the target dropdown.
- Helpers: `addAlias()` pushes `{name:'', target:''}`; `rmAlias(i)` filters it out; both reassign `aliasRows` for reactivity.
- `buildKeyFields()`: `const aliases = rulesToAliases(aliasRows); if (Object.keys(aliases).length) payload.aliases = aliases` — **top-level**, after the existing fields.
- `editKey(k)`: `aliasRows = aliasesToRules(k.aliases)`.
- Reset: on **New key** and **Cancel**, `aliasRows = []` (fold into the existing reset handlers alongside `resetFb()`).
- Template (in the Router Settings body, after the Fallbacks `<label>`):

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
  <span class="hint">Let this key request a name that maps to a real model. The <strong>name</strong> is anything clients send (e.g. <code>gpt-4</code>); the <strong>target</strong> is one of the key's Allowed models. Applied hot, no restart.</span>
</label>
```

## Data flow

```
create/edit key → aliasRows → rulesToAliases → payload.aliases (top-level)
  → keys_routes (passthrough) → /key/generate | /key/update → LiteLLM stores it
edit-open ← aliasesToRules(k.aliases) ← /key/list
```

## Error handling / edge cases

- Empty name or target → row dropped at serialize time.
- Duplicate alias names → last wins (dict).
- Alias name equal to a real model group (shadowing) → allowed; a legitimate use.
- Target constrained to Allowed models by the dropdown source → no unreachable alias.
- Existing keys without `aliases` → empty editor.

## Testing

- **Unit (node, via a copied `.mjs`, like `fallbacks.js`):** `rulesToAliases` (basic, blanks dropped, dedup last-wins, empty→{}); `aliasesToRules` (dict→rows, null/array/junk→[], round-trip).
- **Build:** `npm run build` clean.
- **Playwright (local hybrid stack, ≥2 models):** create a key with an alias → `/api/keys` shows `aliases: {name: target}`; **edit round-trip** loads the row back (this also verifies `/key/update` accepts `aliases`); Cancel/New-key resets the rows.

## Docs

Add a **"Model aliases"** subsection to `docs/admin-ui-guide.md` Router Settings area (parallel to the fallbacks one): what it does, alias-name-is-free-text vs target-must-be-in-Allowed-models, a worked example (`gpt-4` → `gpt-oss-20b`), and "applies hot, no restart". Note it's distinct from `key_alias` (the key's label) and the global `model_group_alias`.

## Out of scope (YAGNI)

- Global `model_group_alias` (router_settings) — this is per-key only.
- Multi-target aliases — LiteLLM aliases are strictly 1:1.
- Any raw-JSON escape hatch — the picker fully represents the flat map.
