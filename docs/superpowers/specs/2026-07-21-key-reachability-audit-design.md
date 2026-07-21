# Key Reachability / Fallback Collision Audit (Phase 2) Design

**Status:** Approved (design), 2026-07-21
**Builds on:** Config Referential Integrity Phase 1 (spec 2026-07-13, shipped 1.30.0) — the pure checker `ui/app/config_integrity.py` and its four consumers. Phase 1 guarantees *references resolve*; this phase answers the deeper question: **can a key reach a model group it was never granted, even when every reference is valid?**

## Problem

LiteLLM applies router fallbacks **without re-checking the per-key ACL**, and its fallback matcher keys on the **failing deployment's provider-stripped base model**, not the requested group (both confirmed in 1.89.2 source and reproduced live during the 2026-07-12 incident: `hindsight` key → `hindsight-llm` → its groq deployment `groq/openai/gpt-oss-20b` failed → parsed `openai/gpt-oss-20b` → stripped `gpt-oss-20b` → matched the fallback → served `gpt-oss-20b-deepinfra`, outside the key's allow-list).

Phase 1 removes *broken* fallbacks (primary not a real group). The residual class: a fallback whose primary IS a valid group still fires for **any deployment anywhere whose stripped base model equals that primary** — so a key allowed group `g` implicitly inherits reach into that fallback's targets whenever `g` contains such a deployment. Every reference is valid; Phase 1 sees nothing. Today the suffixed naming scheme (`-1x`/`-2x`/`-deepinfra`) suppresses this by convention, not construction.

**Decision (locked):** this audit is **advisory only**. A fallback-reachable target can be deliberate — that is what fallbacks are for. The audit makes the true blast radius visible (panel + per-key preview); it never blocks Apply or key saves.

## Goal

1. **Collision audit:** report every (group, deployment, fallback) combination through which a failure can route across group boundaries.
2. **Per-key over-reach preview:** for each virtual key, the exact set of groups it can *also* reach via fallbacks, beyond its allow-list — shown in the report and in the Keys editor.
3. **Fold `model_group_alias` names into `G`** (fixes Phase 1's dormant over-blocking false-positive).

## Grounding facts (verified this session on .75 / litellm 1.89.2)

- `litellm.get_llm_provider("groq/openai/gpt-oss-20b")` → model `"openai/gpt-oss-20b"`, provider `"groq"` (first known-provider prefix consumed).
- `_check_stripped_model_group(model_group, fallback_key)` strips **one** additional `provider/` prefix: `"openai/gpt-oss-20b"` → matches fallback key `"gpt-oss-20b"`.
- `get_fallback_model_group` matching order: exact match on the model-group string, then stripped match, then generic `"*"`. Confirmed empirically: `openai/gpt-oss-20b` → `['gpt-oss-20b-deepinfra']`; `hindsight-llm` → None.
- Router fallbacks (`async_function_with_fallbacks`, router.py:2070) contain **no** reference to `_can_object_call_model` / `user_api_key_dict` — the ACL runs once, pre-router, on the originally requested model.
- `ui_config_applied` model items store `litellm_params.model` in **cleartext** (only LiteLLM's own `ProxyModelTable` is vault-encrypted) — the engine needs no decryption and no live `/model/info` call.
- Phase 1 shipped: `config_integrity.py` (`group_names`, `router_orphans`, `key_orphans`, trims, `_missing`, `_LITELLM_SPECIAL_MODELS`), `GET /api/config/integrity`, Apply-gate, key-save validation, fix endpoint, Routing panel.

## Architecture

### 1. `ui/app/reachability.py` (new, pure — no I/O)

```python
PROVIDER_LIST: frozenset[str]        # vendored snapshot of litellm.provider_list
SEMANTICS_VERSION = "1.89.2"         # litellm version the snapshot + algorithm mirror

def parse_base(model_str) -> dict | None:
    """Mirror get_llm_provider prefix-consumption + _check_stripped_model_group:
    returns {"provider", "parsed", "stripped"} where parsed = model_str minus its
    first known-provider prefix (or model_str unchanged if none), and stripped =
    parsed minus one more known-provider prefix (None if parsed has none).
    Non-str/empty/malformed input -> None (skip, never raise)."""

def match_candidates(deployment_model) -> set[str]:
    """The deployment-derived strings LiteLLM's fallback matcher can see when this
    deployment fails: {parsed} | {stripped if any}."""

def collision_audit(model_items, router_items) -> list[dict]:
    """Per-group failure candidates C(g) = {g} ∪ ⋃ match_candidates(d) over g's
    deployments — the group's own public name is included because the matcher's
    EXACT tier fires on it (a normal fallback {g:[T]} grants un-ACL'd reach into T
    for any key allowed g but not T; the qwen-2x→deepinfra example). For every
    fallback rule {primary: targets} (all variants; default_fallbacks = targets
    reachable from ANY failure): collision iff primary ∈ C(g) and targets ⊄ {g}.
    Record: {"group": g, "deployment_id" (None when matched via the group name
    itself), "base_model", "fallback_setting", "fallback_key": primary,
    "targets": [...]}. Dedup by (group, deployment_id, fallback_setting, primary)."""

def key_over_reach(keys, collisions, groups, mga) -> list[dict]:
    """Pure join. For each key: allowed-groups = models[] resolved (drop alias
    names, resolve mga names via `mga`; specials: all-proxy-models / all-team-models
    => ALL groups, no-default-models => contributes nothing; an EMPTY models list
    => all groups, per LiteLLM's unrestricted-key semantics).
    extra = union of c.targets for collisions with c.group ∈ allowed, minus
    allowed. Emit {"key_alias","token","extra":[{"target","via_group",
    "via_fallback"}]} only when extra is non-empty."""
```

Defensive discipline identical to Phase 1 (`_missing`-style guards; malformed leaves skipped).

### 2. `model_group_alias` names fold into `G` (Phase-1 fix)

`config_integrity.group_names` gains `mga_names: set[str] | None = None` (folded into the returned set when given); each of the four Phase-1 consumers passes the effective `model_group_alias` keys. New tests prove a fallback/key referencing an mga *name* is no longer flagged. (mga *targets* remain checked as before.)

### 3. `GET /api/config/reachability` (new, read-only, `login_required`)

In `config_v3_routes.py`, sibling of `/config/integrity` — deliberately a **separate endpoint** so "references resolve" (gate-backed) and "keys are contained" (advisory) remain distinct claims:

```json
{ "semantics_version": "1.89.2",
  "collisions": [ {"group","deployment_id","base_model","fallback_setting","fallback_key","targets":[...]} ],
  "key_over_reach": [ {"key_alias","token","extra":[{"target","via_group","via_fallback"}]} ] }
```
Inputs: effective model + router items from the config store; keys via `make_keys_client()`. Key-store failure → `{"error":"query_failed","detail":...,"collisions":[...] }` (collisions still returned — they need no key data). Never blocks anything; `in_sync` is not part of this payload.

### 4. UI

- **Routing panel** — a "Reachability (advisory)" subsection beneath the Phase-1 orphan list: amber **info** rows (not the error red), one per collision — *"a failure in `hindsight-llm` (deployment groq/openai/gpt-oss-20b) can route to `gpt-oss-20b-deepinfra` via fallback `gpt-oss-20b`"* — and one per over-reaching key — *"key `hindsight` can also reach `gpt-oss-20b-deepinfra`"*. **No Fix buttons**; a hint line states the two remedies (grant the key the target, or remove/re-scope the fallback) since the right one is operator intent. Clean state: "✓ No cross-group fallback paths." A small "semantics: LiteLLM 1.89.2" caption keeps the version coupling honest.
- **Keys editor** (`Keys.svelte`) — when editing a key that appears in `key_over_reach`, a passive amber line under Allowed models: *"Via fallbacks this key can also reach: `X` (from `Y`)."* Sourced from the same endpoint, fetched once per screen load.

### 5. Fidelity strategy (locked)

Vendored `PROVIDER_LIST` + local ~10-line strip algorithm, **cross-validated against the live proxy at integration time**: the integration step runs every live deployment's `litellm_params.model` through the container's real `litellm.get_llm_provider` (`docker exec python3 -c ...`) and diffs against `parse_base` — any divergence fails the integration. `SEMANTICS_VERSION` is surfaced in the API + panel. On a litellm upgrade, re-running that harness is the documented re-validation step (noted in docs).

### Files

- Create: `ui/app/reachability.py`; `ui/tests/test_reachability.py`.
- Modify: `ui/app/config_integrity.py` (+ tests) for the mga fold; `ui/app/routes/config_v3_routes.py` (+ tests) for the endpoint; `ui/frontend/src/lib/api.js`; `ui/frontend/src/routes/Routing.svelte`; `ui/frontend/src/routes/Keys.svelte`; `docs/admin-ui-guide.md` (Reachability subsection incl. the upgrade re-validation note).

## Error handling

- Deployment model unparsable/malformed → skipped by `parse_base` (None), never a 500.
- Key-store failure → loud `query_failed` with collisions preserved.
- The suite must include a test proving **Apply is never blocked** by a collision (gate untouched) and key saves still pass with over-reach present.

## Testing

- **Unit (`reachability`):** `parse_base` fixture table — the incident case (`groq/openai/gpt-oss-20b` → parsed `openai/gpt-oss-20b`, stripped `gpt-oss-20b`), single-prefix (`deepinfra/Qwen/Qwen3.6-27B`), zero-prefix (`gpt-4o` → parsed unchanged, stripped None), unknown-provider prefix, malformed/None. `collision_audit`: incident topology positive; same-group fallback (targets ⊆ {g}) negative; default_fallbacks; dedup. `key_over_reach`: allow-list resolution (alias names dropped, mga resolved, specials → all), extra-set math, empty-models semantics.
- **Unit (mga fold):** fallback/key referencing an mga name no longer orphaned; mga target still checked; all four Phase-1 consumers pass `mga_names`.
- **Route tests:** shape, auth (401), `query_failed` preserves collisions, no mutation anywhere.
- **Integration (local hybrid stack):** seed the incident topology (group with a foreign-base deployment + a valid-primary fallback + a restricted key) → reachability reports the collision and names the key's exact extra target; Apply succeeds (advisory proven); cross-validation harness diffs `parse_base` vs live `get_llm_provider` for all deployments → zero divergence.

## Out of scope (YAGNI)

- Any blocking/gating on collisions (locked: advisory only) and any auto-fix action for them.
- Modelling LiteLLM's cooldown/retry timing, `context_window`/`content_policy` *trigger conditions* (their targets are audited identically; when they fire is irrelevant to reachability).
- Team-scoped ACLs (`all-team-models` treated as all groups — conservative over-report; no team data in this deployment).
- Live provider_list API sync (re-validation is the documented upgrade step instead).
