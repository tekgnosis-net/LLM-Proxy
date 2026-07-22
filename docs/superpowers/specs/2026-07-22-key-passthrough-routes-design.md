# Per-Key Allowed Passthrough Routes (UI) Design

**Status:** Approved (design), 2026-07-22
**Builds on:** the Virtual Keys editor (per-key models/aliases/fallbacks pickers, 1.26.0–1.28.1) and `keys_routes` passthrough to `/key/generate|/key/update`.
**Motivating incident:** a virtual key calling the staged Kokoro pass-through (`/v1/audio/voices`, `auth: true`) 403s — *"Key/team not allowed to access passthrough route … Configure `allowed_passthrough_routes`"* — and the Virtual Keys form has no field for it.

## Problem & the key finding (verified live on .75 / litellm 1.89.2 OSS)

LiteLLM gates `auth: true` pass-through routes with a per-key/team allow-list, `allowed_passthrough_routes`, enforced in the **open-source** package (`route_checks._require_auth_pass_through_access` → `check_passthrough_route_access`, which reads `user_api_key_dict.metadata["allowed_passthrough_routes"]` and matches **exact-or-prefix**). The master key (proxy admin) bypasses the gate; a virtual key does not.

**The trap:** the *enforcement* (deny) is OSS, but setting the **top-level** `allowed_passthrough_routes` param on `/key/generate|update` is **Enterprise-gated** — on OSS it 403s: *"This feature is only available for LiteLLM Enterprise users… set `LITELLM_LICENSE`"* (no license on .75). **However**, setting it under **`metadata.allowed_passthrough_routes`** is NOT Enterprise-gated on OSS and IS exactly what the enforcer reads — verified: a key created with `metadata:{allowed_passthrough_routes:[…]}` succeeds and round-trips. So the OSS-viable mechanism is the **metadata sub-key**, not the top-level field.

**Second verified constraint:** `/key/update` **REPLACES** the metadata dict (a pre-existing metadata key is wiped when update sends a metadata with only the new sub-key). So the UI MUST read-merge-write: preserve the key's other metadata.

## Goal

Add an **"Allowed passthrough routes"** row-picker to the Virtual Keys editor that reads/writes `metadata.allowed_passthrough_routes` (merged into the key's existing metadata), so an operator can grant a key access to `auth:true` pass-through routes (e.g. `/v1/audio/voices`) without hand-editing the DB — on open-source LiteLLM.

## Grounding facts

- **Match is exact-or-prefix** (`_route_matches_allowed_route`: `route == allowed` or `route.startswith(allowed + "/")`). One entry `"/v1/audio/voices"` authorizes both `/v1/audio/voices` and `/v1/audio/voices/combine` — aligns with the passthrough's `include_subpath: true`.
- **Admin-only to set** (`_check_passthrough_routes_caller_permission`, top-level OR metadata): only PROXY_ADMIN. The UI calls `/key/*` with the **master key = admin**, so it's permitted.
- **Round-trip:** the value lives in `metadata.allowed_passthrough_routes` (top-level `allowed_passthrough_routes` is Enterprise-only and unused here); `/key/list` (via `keys_client.list_keys`, `return_full_object=true`) returns the key's `metadata`.
- **keys_routes** passes the payload through unchanged; the Phase-1 `_validate_key_refs` reads only `models`/`aliases` — it ignores `metadata`, so no interference.
- The UI's `buildKeyFields()` currently does **not** send `metadata` at all — introducing it means we must preserve any metadata the key already has.

## Architecture

### Frontend (the whole feature — no backend change)

`ui/frontend/src/routes/Keys.svelte`:
- **State:** `passthroughRows: string[]` (one route per row); a private `existingMetadata` object captured on edit so update preserves it. Both reset in `resetFb()`.
- **UI:** an "Allowed passthrough routes" section directly under Allowed models — repeating rows of a single text input with **✕**, plus **+ Add route**. Help text: *"Routes this key may reach on the proxy's pass-through endpoints (e.g. `/v1/audio/voices`). Prefix-matched — `/v1/audio/voices` also allows `/v1/audio/voices/combine`."* Shown for every key (empty by default).
- **`buildKeyFields()`:** build `routes = passthroughRows.map(trim).filter(Boolean)` (dedup, drop blanks). Compute `metadata = { ...existingMetadata, allowed_passthrough_routes: routes }` (drop the sub-key entirely when `routes` is empty, to keep clean metadata). **Always send `payload.metadata = metadata`** so update's replace-semantics preserve other keys AND clearing all rows removes the sub-key. (If `existingMetadata` is empty and `routes` is empty, omit `metadata` so we don't write `{}` on brand-new keys with nothing else.)
- **`editKey(k)`:** `existingMetadata = { ...(k.metadata||{}) }`; `passthroughRows = [...(k.metadata?.allowed_passthrough_routes || [])]`. (No injection into allowed-models — this is a route ACL, unrelated to the model ACL.)
- Small pure helper `passthroughRowsToList(rows)` (trim/dedup/drop-blank) — node-testable, mirroring `aliases.js`.

### Backend

None. `keys_routes.create_key/update_key` already forward the full payload (incl. `metadata`) to litellm; `_validate_key_refs` is unaffected.

### Files

- Modify: `ui/frontend/src/routes/Keys.svelte`; create `ui/frontend/src/lib/passthrough.js` (+ node sanity test) for `passthroughRowsToList`.
- Modify: `docs/admin-ui-guide.md` (Virtual Keys → passthrough-routes subsection, incl. the metadata/OSS note).

## Error handling & edge cases

- **Metadata preservation:** never send a metadata that drops the key's existing keys (the read-merge-write above). Covered by an integration assertion.
- **Empty:** no rows + no prior metadata → omit `metadata` (don't write `{}`); no rows + prior metadata → send prior metadata minus the sub-key.
- **Bad input:** blank/whitespace rows dropped; duplicates collapsed. No hard format validation (wildcards/prefixes are legitimate); a soft hint if a value doesn't start with `/` is optional, not required.

## Testing

- **Node unit** (`passthrough.js`): trim, drop-blank, dedup, empty→`[]`.
- **Playwright (local hybrid stack)** — the end-to-end proof:
  1. Stage+Apply an `auth:true` passthrough (`/v1/audio/voices` → a stub target); create a **restricted** virtual key; call the route with it → **403** (baseline).
  2. Edit the key in the UI, add `/v1/audio/voices`, Save → `/api/keys` shows `metadata.allowed_passthrough_routes:["/v1/audio/voices"]`.
  3. Re-call the route with the key → **200 / not 403** (enforcement satisfied via metadata).
  4. `/v1/audio/voices/combine` with the key → also passes (prefix match).
  5. **Metadata preservation:** give the key a second metadata key out-of-band, edit+save via UI, confirm the other key survives.
  6. Clear the row + Save → `metadata.allowed_passthrough_routes` gone; route 403s again.

## Out of scope (YAGNI)

- The Enterprise **top-level** `allowed_passthrough_routes` param (blocked on OSS; we use metadata).
- Auto-discovering configured passthrough routes to offer as a dropdown (couples Keys to the passthrough config; free-text matches how LiteLLM models it).
- Team-level `allowed_passthrough_routes` (teams aren't exposed in this UI).

## Durability note (surface in docs)

This rides on an asymmetry in LiteLLM's OSS build: the Enterprise gate guards the *top-level* param but not `metadata.allowed_passthrough_routes`, which the OSS enforcer reads. It works today (1.89.2, verified) and is the only OSS-native way to grant a virtual key passthrough access. A future litellm could extend the gate to metadata; if that happens the fallback is `auth: false` on the passthrough (no key/ACL) or an Enterprise license. Documented so a future maintainer isn't surprised.
