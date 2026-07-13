# Config Referential Integrity (Phase 1) Design

**Status:** Approved (design), 2026-07-13
**Builds on:** the hybrid master/servant config engine (`config_db`/`config_engine`/`config_render`), the drift/resync pattern (1.22.0/1.23.0), and the per-key alias workaround (1.28.1).
**Follow-up (committed, separate spec):** Phase 2 — reachability / base-model collision audit + per-key "can also reach…" preview. Explicitly **out of scope here** (see Out of Scope).

## Problem

The hybrid config layer is a **flat, reference-blind key-value store**. A model group's public name (`model_name`) is referenced from several places, but nothing validates those references, so ordinary rename/delete operations silently leave **dangling references**. This is not hypothetical: a `router_settings.fallbacks` rule `{gpt-oss-20b: [gpt-oss-20b-deepinfra]}` survived the rename of `gpt-oss-20b` → `gpt-oss-20b-1x`, and — via LiteLLM's provider-stripped fallback matching, which does **not** re-check per-key ACLs — let the `hindsight` key receive (and get billed for) output from `gpt-oss-20b-deepinfra`, a group not in its allow-list. The orphaned fallback has been removed operationally; this feature prevents the class from recurring and gives operators a way to find and fix such records.

## Goal

Add **Detect → Prevent → Fix** for broken (dangling) config references:
- **Detect:** a read-only integrity report listing every reference that names a group which does not exist.
- **Prevent:** a hard Apply-gate for `ui_config`-owned references, and key-save validation for the litellm-owned key references.
- **Fix:** per-orphan, review-then-fix removal of the dangling reference (repoint is an optional extension).

## Grounding facts (verified in-repo, 2026-07-13)

- **Valid group set `G`** = distinct `model_name` across the effective (applied⊕staged, non-deleted) model items in `ui_config`. This is master-authoritative and works in both hybrid and non-hybrid modes (model items live in `ui_config` regardless of `STORE_MODEL_IN_DB`).
- **Two stores hold references:**
  - *ui_config* (we own): `router_settings.fallbacks`, `context_window_fallbacks`, `content_policy_fallbacks`, `default_fallbacks` (each a list of `{primary: [targets]}`) and `model_group_alias` (`{alias: target}`). Stored as opaque `kind=router_setting` rows; rendered verbatim by `config_render.render_config`.
  - *litellm `LiteLLM_VerificationToken`* (separate store, reached via `keys_client`): per-key `models` (allow-list) and `aliases` (`{name: target}`). `keys_client.list_keys()` returns full objects incl. `key_alias`, `token`, `models`, `aliases`; `keys_client.update_key(payload)` posts `/key/update`, which **replaces** the sent fields (1.28.1).
- **Reference rules** (what makes something an orphan):
  - Fallback (all variants): every **primary** and every **target** must be in `G`.
  - `model_group_alias`: every **target** must be in `G`; the alias **name** is a new public name and is exempt.
  - Per-key `models[]`: each entry must be in `G` **or** be one of that key's own `aliases` names — the latter is the legitimate 1.28.1 injection (`withAliasNames`), not an orphan.
  - Per-key `aliases{name:target}`: each **target** must be in `G`.
- **Apply flow** (`config_engine.apply_config`): renders effective config → `validate_config` (ssl/routing enums) → atomic write → `fold()` → reconcile/restart. The integrity gate slots in **pre-commit**, right after `eff` is computed, alongside `validate_config` — raise `ApplyError` (→ 422) before any file write or fold, so nothing is committed.
- **Existing patterns to mirror:** `/api/config/drift` (read-only, `{in_sync, ...}`, `query_failed` guard) and its Models badge; the staged→Apply model (router-setting changes require the ~25s restart; model/key changes are hot).

## Architecture (Detect → Prevent → Fix)

### 1. `app/config_integrity.py` (new, pure — the single source of truth)

Pure functions, no I/O, fully unit-testable. Reused by the endpoint, both gates, and the fix.

```python
def group_names(model_items: list[dict]) -> set[str]:
    """Distinct public model_name across non-deleted model items (effective)."""

# Each orphan is a dict shaped for both UI rendering and deterministic fixing:
#   {"scope": "router"|"key",
#    "location": str,                 # human: "router_settings.fallbacks" | "key 'hindsight' → allowed models"
#    "reference": str,                # the dangling group name
#    "missing": [str],                # group name(s) not in G (usually [reference])
#    "target": {...}}                 # machine handle the fix endpoint uses (see below)

def router_orphans(router_items: list[dict], groups: set[str]) -> list[dict]:
    """Scan fallbacks / context_window_fallbacks / content_policy_fallbacks /
    default_fallbacks (list[{primary:[targets]}]) and model_group_alias ({alias:target}).
    target = {"setting": <router_setting name>, "kind": "fallback_rule"|"mga_entry",
              "primary": <primary or alias>, "dangling": <the missing name>}."""

def key_orphans(keys: list[dict], groups: set[str]) -> list[dict]:
    """Per key: models[] entries not in G and not in that key's alias names; alias
    targets not in G. target = {"token": <key token>, "field": "models"|"aliases",
                                "entry": <the dead model name or alias name>}."""
```

Defensive: tolerate malformed shapes (a fallback that isn't a list, `aliases` that isn't a dict) → skip, never raise. Provider-stripping is **not** modelled here — that's Phase 2; here a reference is an orphan iff its literal name ∉ `G`.

### 2. Detect — `GET /api/config/integrity`

Sibling of `/config/drift` in `config_v3_routes.py`.
- `groups = group_names(effective model items)`; `router_orphans(effective router items, groups)`; `key_orphans(await keys_client.list_keys(), groups)`.
- Returns `{"in_sync": bool, "router_orphans": [...], "key_orphans": [...]}`. On a key-store fetch failure, return `{"error": "query_failed", "detail": ...}` (loud, like drift) rather than a false "in_sync".
- Read-only.

### 3. Prevent

- **Apply-gate** (`config_engine.apply_config`): after `eff` is computed, compute `groups` + `router_orphans(eff router items, groups)`; if non-empty, `raise ApplyError("integrity: <setting> references missing group(s) <names>; fix in the Integrity panel")`. Pre-commit for **both** hybrid and non-hybrid, before validate/write/fold. Scope = router refs only (keys are not part of the apply flow).
- **Key-save validation** (`keys_routes.create_key`/`update_key`): before passthrough to litellm, compute `groups` (from the config store's effective model items) and reject (422) if `payload.models` (excluding the payload's own alias names) or `payload.aliases` targets name anything ∉ `G`. Prevents *new* key orphans. A small helper `make_config_store()` read is added to `keys_routes`.

### 4. Fix — `POST /api/config/integrity/fix`

Body: one orphan record (from the report) + `dry_run: bool`.
- **`dry_run: true`** → returns `{"before": ..., "after": ..., "effect": "stages a config change (needs Apply + restart)" | "applies immediately (hot)"}`. No mutation. Powers the UI's review step.
- **`dry_run: false`** → performs the removal:
  - *Router orphan:* load the `router_setting` item and remove **only the dangling name**, at the right granularity: a dangling **fallback primary** → drop the whole `{primary:[…]}` rule; a dangling **fallback target** → drop just that target from its list (and if the list becomes empty, drop the rule); a dangling **`model_group_alias` target** → drop that `{alias:target}` entry. (The orphan's `target` handle carries `primary` + `dangling`, so the fix knows which case it is.) If the whole setting becomes empty → `store.stage(kind, name, {}, deleted=True)`; else `store.stage(...)` the trimmed value. Returns `{"staged": true, "needs_apply": true}`. Operator Applies (restart) — honest about the cost.
  - *Key orphan:* build a `/key/update` payload that removes the dead entry from `models`/`aliases` (send the full trimmed field, since `/key/update` replaces) and call `keys_client.update_key`. Returns `{"applied": true, "needs_apply": false}` (hot, no restart).
- **Removal is the only action** in Phase 1. (Optional future: a `repoint` action for key allow-lists where substituting a live group is meaningful — deferred; per the gpt-oss case, remove is the intended fix.)

### 5. UI — Integrity panel

On the **Routing** screen, beside Router Settings, with a count **badge** (same visual language as the Models drift badge).
- Fetches `/api/config/integrity` on mount and after Apply / key edits.
- Lists orphans grouped by scope; each row: the dangling reference + location + a **Fix** button.
- Fix flow: click → call fix `dry_run:true` → show before/after + the effect note (router = "will stage a change, needs Apply"; key = "applies immediately") → confirm → call fix `dry_run:false` → re-fetch report.
- Clean state: a quiet "✓ No dangling references."

### Files

- Create: `ui/app/config_integrity.py`; `ui/tests/test_config_integrity.py`.
- Modify: `ui/app/config_engine.py` (Apply-gate); `ui/app/routes/config_v3_routes.py` (integrity + fix endpoints); `ui/app/routes/keys_routes.py` (key-save validation, config-store read); `ui/tests/test_config_v3_routes.py` (or equivalent) + keys route tests.
- Modify: `ui/frontend/src/routes/Routing.svelte` (integrity panel + badge); `ui/frontend/src/lib/api.js` (integrity + fix calls).
- Modify: `docs/admin-ui-guide.md` (Routing → Integrity subsection); `docs/config-schema.md` (note the reference rules).

## Error handling

- Integrity endpoint: key-store fetch failure → `{"error":"query_failed"}` (never a silent "in_sync"). Malformed router/alias shapes → skipped by the pure checker, never a 500.
- Apply-gate: raises `ApplyError` → 422 with the offending setting + missing names; nothing written/folded (pre-commit).
- Fix: unknown/duplicate orphan (already fixed since the report was fetched) → 409/idempotent no-op with a clear message; the UI re-fetches. Key `/key/update` failure → surfaced, report unchanged.

## Testing

- **Unit (`config_integrity`):** `group_names` (dedup, deleted excluded); `router_orphans` — fallback primary missing, fallback target missing, all variants, `model_group_alias` target missing, clean, malformed-shape tolerance; `key_orphans` — allowed-model missing, **alias-name-injection exemption** (a `models` entry that is one of the key's alias names is NOT an orphan), alias target missing, clean.
- **Routes:** integrity endpoint shape + `query_failed` guard; Apply-gate 422 (orphaned fallback blocks apply, nothing folded); key-save 422 (dead group in `models`/`aliases`); fix router (dry-run preview; real fix stages / stages-delete when empty); fix key (dry-run; real fix calls `update_key` with the trimmed field).
- **Playwright (local hybrid stack):** seed one router orphan (fallback → dead group) + one key orphan (allow-list → dead group) → panel shows 2 → Fix each (router: stage → Apply; key: hot) → re-scan clean; separately, confirm the Apply-gate blocks applying a config whose fallback names a dead group.

## Out of scope (deferred to Phase 2 — committed follow-up)

- **Base-model collision audit:** a group containing a deployment whose provider-stripped base model collides with a *valid* group name used as a fallback primary (the residual path where every reference is valid yet a key still over-reaches). Requires faithfully mirroring LiteLLM's `get_llm_provider` + `_check_stripped_model_group` (version-coupled) and each deployment's decrypted underlying model — its own adversarial test matrix, ideally cross-validated against the live router.
- **Per-key reachability preview** ("this key can also reach X via fallback from Y") — the operator-facing surface of the same engine.
- **Auto-cascade on rename** (auto-rewriting references when a group is renamed) and the **repoint** fix action — Phase 1 detects and removes; it does not rewrite.
- **Proactive inline rename/delete warning** — the panel surfaces the resulting orphan after the change and the Apply-gate blocks committing it; a pre-emptive "referenced by…" note at stage time is a later nicety.
