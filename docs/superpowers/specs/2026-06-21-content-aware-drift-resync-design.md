# 1.23.0 — Content-aware Drift & Resync Design

**Status:** Approved (design), 2026-06-21
**Builds on:** 1.22.0 (presence-only drift indicator + Resync), 1.21.1 (model_info.id-keyed reconcile), 1.21.0 (hybrid hot-apply).

## Goal

Make the hybrid drift indicator and Resync **content-aware**: detect and converge models whose `model_info` *contents* differ between ui_config (the Master's intent) and litellm's live deployment — not just models that are present/absent. Fix the underlying LiteLLM `/model/update` bug that makes `model_info` changes impossible to persist on an existing model.

## Motivation (root cause, verified two ways)

A user disabled the background health check on the **groq** `gpt-oss-20b` deployment (id `f0131005`) on the live host. The UI's applied state correctly held `model_info.disable_background_health_check = true` and the global gate `general_settings.health_check_skip_disabled_background_models = true`, but litellm's **live** deployment kept `disable_background_health_check = None`, so the (billed) background probe kept hitting groq.

- **Code proof:** LiteLLM's old `POST /model/update` handler writes only `{"litellm_params": …, "updated_by": …}` to `LiteLLM_ProxyModelTable` — it **ignores `model_info` entirely**. Its newer `PATCH /model/{model_id}/update` *does* persist `model_info` (`prisma_compatible_model_dict["model_info"] = json.dumps(model_info)`).
- **Live repro (Playwright):** ticking "Disable background health check" on a model → Save → Apply (restart, `/model/update` fired) left litellm's `disable_background_health_check` at `None`. No number of Save→Apply cycles can ever work for an *existing* model.

Why only groq: deepinfra deployments received the flag at **creation** (`/model/new` persists `model_info`); groq existed first and the flag was added later via **update** (the broken path). Asymmetry is create-vs-update, not provider-specific.

Why 1.22.0 didn't catch it: drift/resync are **presence-only** (compare the *set* of `model_info.id`s). The groq id exists on both sides → "in sync", while a field inside it is stale.

## Global Constraints

- Hybrid-only feature (`STORE_MODEL_IN_DB=true`); config-only mode returns `{hybrid:false,in_sync:true}` for drift and 422 for resync (unchanged from 1.22.0).
- All model comparison stays in **`model_info.id`-space** (the 1.21.1 invariant). `diff_models` stays pure and is reused.
- No literal secrets in `config.yaml`; credentials remain inline-resolved into hot calls. The convergence path never logs or returns secrets.
- The drift badge must not raise false positives: only **UI-managed** `model_info` fields are compared; litellm-derived fields (`created_at`, `updated_at`, `db_model`, encrypted keys, etc.) are never inspected.
- Resync remains **preview → confirm → converge**; deletions only on confirmation (unchanged from 1.22.0).
- No change to normal **Apply** semantics: Apply pushes staged intent only. **Resync** is the out-of-band full-convergence action.

## Components

### 1. `app/model_content.py` — single source of truth for UI-managed model_info

The one place that defines *what the UI manages inside `model_info`* and *how to normalize it*. Imported by both the comparator (read) and the convergence payload builder (write) so they can never diverge.

```python
# Allowlist of UI-managed model_info fields, each with a normalizer + default.
MANAGED_MODEL_INFO = {
    "disable_background_health_check": {"norm": lambda v: bool(v), "default": False},
}

def normalized_managed(model_info: dict) -> dict:
    """Return {field: normalized_value} for every managed field, applying defaults
    for absent fields. litellm-derived fields are ignored."""

def content_diff(desired_mi: dict, live_mi: dict) -> list[str]:
    """Return the sorted list of managed field names whose normalized values differ.
    Empty list == content in sync."""

def managed_patch_fields(desired_mi: dict) -> dict:
    """Return {field: normalized_value} to send EXPLICITLY in a PATCH payload so
    merge-semantics overwrite in both directions (e.g. disable=false, not omitted)."""
```

- **Interfaces.** Consumes a `model_info` dict; produces a normalized subset / diff / explicit-patch subset. Depends on nothing else.

### 2. `models_client.update_model` — use the PATCH endpoint

Switch from `POST /model/update` (drops `model_info`) to `PATCH /model/{model_id}/update` (persists it).

- `model_id` is taken from `payload["model_info"]["id"]` (always present after render — the 1.21.1 invariant).
- Body is the existing rendered entry (`model_name`, `litellm_params`, `model_info`), with the managed `model_info` fields set **explicitly** via `managed_patch_fields` so un-ticking converges, not just ticking.
- Path-encode `model_id`.

### 3. `GET /api/config/drift` — add `content_drifted`

Shape becomes:

```json
{ "hybrid": true,
  "in_sync": false,
  "missing_in_litellm": [{"id":"…","model_name":"…"}],
  "extra_in_litellm":   [{"id":"…","model_name":"…"}],
  "content_drifted":    [{"id":"…","model_name":"…","fields":["disable_background_health_check"]}] }
```

- `in_sync == (missing == extra == content_drifted == [])`.
- Content comparison: for each id present in **both** desired (applied, rendered) and live, `content_diff(desired_mi, live_mi)`; non-empty → a `content_drifted` entry. Uses `resolve_key=None` for the presence/content read (no secret needed; drift never resolves credentials — consistent with 1.22.0). **Verified safe:** `render_model_entry(it, resolve_key=None)` builds the full `model_info` and skips credential resolution entirely (the `if resolve_key is not None` guard), so credentialed models are never dropped from the content read.

### 4. `POST /api/config/resync` — converge content too

- Resync calls `reconcile_models(..., converge_content=True)`.
- **`reconcile_models` gains `converge_content: bool = False`.** When `True`, after `build_desired`, it computes the content-drifted ids — `{id for id in (desired ∩ live) if content_diff(desired[id].model_info, live_by_id[id].model_info)}` — and **unions them into the update set** before `diff_models` (alongside the existing `changed_ids ∪ force_ids`). When `False` (the default, used by **Apply**), behavior is exactly as today: update only staged-changed / credential-rotated ids. This is the single switch that keeps Apply staged-only while Resync forces content convergence.
- `diff_models` stays pure and unchanged — the content-drifted ids are merged into the `changed_ids` set passed to it.
- Converge: add missing, delete extras (on confirmation), and **PATCH** content-drifted models (via the fixed `update_model` + `managed_patch_fields`).
- Result/report extends the existing `{added, updated, deleted, failed}` shape; PATCH failures land in `failed[]` per-model.

### 5. Models screen — badge + preview

- Badge count = `missing + extra + content_drifted` length; "In sync ✓" when all empty (text/threshold logic unchanged, just a larger count source).
- Resync preview string: `+ add N / ~ update M / - delete K`. `~ update` lists content-drifted public names. Confirm-before-delete unchanged.

## Data flow

```
Apply (staged intent)                 Resync (full convergence, out-of-band)
  reconcile_models(converge_content=    reconcile_models(converge_content=True)
    False)                               update set = (changed ∪ force) ∪ content_drifted
   update set = staged-changed ∪          → add missing / PATCH drifted / delete extras (confirmed)
     credential-rotated                   (now persists model_info)
   → PATCH /model/{id}/update
   (now persists model_info)
```

Both write paths funnel through `update_model` → `PATCH /model/{id}/update`, so model_info persists everywhere. The only difference is which ids land in the update set, controlled by `converge_content`.

## Error handling

- PATCH failure → `failed[]` entry `{id, op:"update", error}`, surfaced in the resync result banner (consistent with add/delete today). Never rolled back; fix-forward.
- Drift query failure → `{error:"query_failed"}` (unchanged).
- Config-only mode → drift `{hybrid:false,in_sync:true}`, resync `422` (unchanged).

## Testing

- **Unit (`model_content`):** normalization (bool coercion, absent→default), `content_diff` (drift / no-drift / normalization-equivalent), `managed_patch_fields` emits explicit values.
- **Unit (client):** `update_model` issues `PATCH /model/{id}/update` with the managed fields explicit (httpx transport mock asserts method, URL, body).
- **Unit (reconcile):** content-drifted id routed to update; mixed add/update/delete plan.
- **Endpoint:** drift returns `content_drifted` for a model that differs only in `model_info`; resync issues the PATCH and clears it; config-only safety preserved.
- **Playwright:** the exact repro — toggle "Disable background health check" → Apply → litellm now shows the flag; plus inject a content drift → badge "⚠ N out of sync" → Resync (`~ update`) → "In sync ✓".

## Out of scope (YAGNI / follow-ups)

- **litellm_params content drift** (api_base/model edited directly via litellm's API). The old `/model/update` already persists litellm_params, so the UI's managed-param changes converge via the normal path; only `model_info` was the gap. Comparing litellm_params needs heavy normalization (encrypted keys, type coercion) and risks false positives — deferred.
- **Force-converging all content on every Apply.** Apply stays staged-only; Resync is the full-convergence tool.

## Rides along in the 1.23.0 release

- **Timezone fixes** (already implemented, branch `v3.14-timezone-fixes`): Usage emits UTC-aware ISO (`+00:00`) so the browser converts to local; Logs reformats Docker's `Z` timestamp to local at ingest. Verified on a Sydney (UTC+10) browser.
- **groq repair:** on deploy, a Resync detects groq's `disable_background_health_check` content drift and PATCHes it — repairing the live host through the normal feature path (no manual surgery).
