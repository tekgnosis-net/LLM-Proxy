# LLM-Proxy Admin UI — Model Drift Robustness (Design)

**Status:** design (brainstormed & approved 2026-06-21). Builds on shipped `1.21.0` (hybrid hot-apply). Ships as **`1.21.1`** (engine correctness patch) then **`1.22.0`** (drift indicator + Resync).

**Why:** the `1.21.0` hybrid reconcile keys models by the wrong identity, which produced a real production incident: a model item whose stored `model_info.id` (`ad27a440…`) differed from its `ui_config` key (`f0131005…`) was perpetually mis-classified as "to add," never converged into litellm, and emitted recurring `Unique constraint … (model_id)` / "Failed to add model to db" errors. The same keyspace confusion **also silently disables credential-rotation force-update**. And there is **no way to see drift** between `ui_config` (master) and litellm's model DB except a manual `psql`/`curl` id comparison. This batch (a) makes the reconciler's identity handling correct, and (b) makes drift visible and one-click healable.

**Scope:** **model** drift only — `ui_config` applied models ↔ litellm's `LiteLLM_ProxyModelTable` (via `/model/info`). Settings (`router/litellm/general`) live in `config.yaml` (a rendered artifact, different mechanism) and are **out of scope**.

**Decided in brainstorming (approved):**
- Drift = compare by **`model_info.id`** via a **dedicated backend endpoint** `GET /api/config/drift` (one source feeding both the badge and the Resync preview; the UI backend already holds the master key).
- **Resync = preview + confirm, then full converge** (add missing + delete extras; deletes only on explicit confirmation).
- **Release split:** engine patch `1.21.1` first (correctness, deploy promptly — fixes the live credential-rotation bug too); drift+Resync UI as `1.22.0` on top.

---

## 1. Current architecture (what we're fixing)

`1.21.0` hybrid Apply runs `reconcile_models` (`ui/app/model_reconcile.py`):
- `desired[it["name"]] = render_model_entry(it, resolve_key)` — **keyed by ui_config item name**.
- `diff_models(desired, live, changed_ids, force_ids)`: `live_ids` = `model_info.id` from `/model/info`; `to_add = desired_ids − live_ids`; `to_delete = live_ids − desired_ids`; `to_update = (changed_ids | force_ids) & (desired_ids & live_ids)`.
- `apply_config` (`config_engine.py:104-132`) passes `changed_ids` = staged **model item names**, `force_ids` = `creds_changed` = staged **credential names**.

**Three defects, one root cause (keyspace confusion):**
1. **Wrong desired key.** `desired` is keyed by item *name*, but `live` is keyed by `model_info.id`. When `name == model_info.id` (normal models) it works; when they diverge (legacy/import items — `render_model_entry` keeps an explicit `data.model_info.id`), the item is always in `to_add` and never converges → drift + unique-constraint errors. **And `to_delete` could remove a live model whose id isn't among the *names* — a latent wrongful-delete.**
2. **Credential-rotation never fires.** `force_ids` = credential *names*, intersected with `desired_ids` = model keys → always ∅. A rotated credential's models are never force-updated → they keep the stale inlined key.
3. **Add not idempotent.** A `/model/new` for an id already in litellm's DB (transient `/model/info`-vs-DB lag, or the defect-1 case) returns a 500 unique-constraint that we record as a hard failure instead of converging.

---

## 2. Piece 1 — Engine correctness patch (`1.21.1`)

**File:** `ui/app/model_reconcile.py` (logic), with a clarifying rename at the `apply_config` call site (`config_engine.py:131`). `diff_models` stays **pure and unchanged** — we only fix what is fed into it.

### 2.1 Key `desired` by `model_info.id`
In `reconcile_models`, build the desired map keyed by the rendered entry's id:
```python
entry = render_model_entry(it, resolve_key)        # may raise KeyError (missing cred) → failed
mid = entry["model_info"]["id"]
desired[mid] = entry
name_to_id[it["name"]] = mid                        # for translating staged signals
```
Now `desired` keys and `live` ids are the same space (`model_info.id`), so `diff_models` classifies correctly and `to_delete` can never target a model that's actually desired.

### 2.2 Translate staged signals into id-space
`reconcile_models` accepts the staged signals as *names* and resolves them to ids internally:
```python
def reconcile_models(desired_items, live, client, *, changed_item_names, creds_changed, resolve_key):
    # build desired{} + name_to_id{} (above); items that fail key-resolve → failed[]
    changed_ids = {name_to_id[n] for n in changed_item_names if n in name_to_id}
    force_ids = {                                   # models referencing a rotated credential
        name_to_id[it["name"]]
        for it in desired_items
        if (it["data"].get("litellm_params") or {}).get("litellm_credential_name") in creds_changed
        and it["name"] in name_to_id
    }
    plan = diff_models(desired, live, changed_ids, force_ids)
    ...
```
`apply_config` passes `changed_item_names=changed_ids` (its staged model names) and `creds_changed=creds_changed` — **no behavior change at the call site beyond the keyword names**. This fixes credential-rotation force-update (defect 2).

### 2.3 Idempotent add
In the `to_add` loop, treat a unique-constraint / "already exists" error as convergence, not failure:
```python
for entry in plan["to_add"]:
    try:
        await client.add_model(entry); added += 1
    except Exception as e:
        if _is_already_exists(e):                   # 'unique constraint' / 'already exists' (case-insensitive on str(e))
            try:
                await client.update_model(entry); updated += 1
            except Exception as e2:
                failed.append({"id": entry["model_info"]["id"], "op": "add->update", "error": str(e2)})
        else:
            failed.append({"id": entry["model_info"]["id"], "op": "add", "error": str(e)})
```
`_is_already_exists(e)` matches `"unique constraint"` or `"already exists"` in `str(e).lower()` (covers the litellm/prisma `Failed to add model to db` 500 + the constraint text). Fixes defect 3 and the recurring log noise.

### 2.4 Tests (TDD, pure where possible)
- `diff_models` stays pure/unchanged → its existing tests stay green untouched.
- `reconcile_models` signature changes to keyword args `changed_item_names`/`creds_changed` (was `changed_ids`/`force_ids`); update the existing `reconcile_models` test call sites + the `apply_config` call site to the new keywords (the empty-set cases keep passing — behavior only changes when names/cred-names are non-empty).
- New `reconcile_models` tests: (a) item with `data.model_info.id` ≠ `name` → keyed by the id, converges (no perpetual to_add); (b) a `creds_changed` credential → the model(s) referencing it appear in `to_update` (force-update fires); (c) `to_add` whose id already exists → `add_model` raises constraint → falls back to `update_model`, counted as updated, not failed; (d) `to_delete` never includes a desired model even when `name ≠ model_info.id`.

---

## 3. Shared diff helper (used by Apply, drift, Resync)

`diff_models(desired: dict[id→entry], live, changed_ids, force_ids)` already returns `{to_add (entries), to_update (entries), to_delete (ids)}`. Both the drift endpoint and Resync reuse it:
- **Drift (read-only):** `diff_models(desired, live, set(), set())` → `to_add` = missing-in-litellm, `to_delete` = extra-in-litellm. No client calls.
- **Resync (write):** `reconcile_models(applied_models, live, client, changed_item_names=set(), creds_changed=set(), …)` → adds missing + deletes extras (`to_update` empty by construction). Param-updates remain an Apply concern (the masked-`api_key` problem makes param-diff unreliable; presence-convergence is what "drift" means here).

---

## 4. Piece 2 — Drift indicator (`1.22.0`)

### 4.1 Backend `GET /api/config/drift` (login-gated)
`ui/app/routes/config_v3_routes.py`. Hybrid-only:
```json
{ "hybrid": true, "in_sync": false,
  "missing_in_litellm": [{"id":"…","model_name":"gpt-oss-20b"}],
  "extra_in_litellm":  [{"id":"…","model_name":"rogue-x"}] }
```
- Build `desired` from applied models keyed by `model_info.id` (via `render_model_entry`, `resolve_key=None` — read-only, no secret needed for presence).
- `live` = `models_client.list_models()`.
- `plan = diff_models(desired, live, set(), set())`; `missing` from `plan["to_add"]` (id + model_name), `extra` from `plan["to_delete"]` (id + model_name looked up in `live`). `in_sync = not missing and not extra`.
- Config-only mode (`store_model_in_db=false`): return `{"hybrid": false, "in_sync": true}` — N/A (models live in the file).
- On `/model/info` fetch error: `{"error":"query_failed"}` (never a false "in sync"), mirroring the Usage-dashboard guard.

### 4.2 Frontend `Models.svelte`
- On mount (and after any successful Apply or Resync), `api.drift()` → header badge:
  - in_sync → **"In sync ✓"** (green/muted).
  - drift → **"⚠ N models out of sync"** (amber), expandable to list missing (defined but not served) + extra (served but not defined).
- Hidden entirely in config-only mode (`hybrid:false`).

---

## 5. Piece 3 — Resync (`1.22.0`)

### 5.1 Backend `POST /api/config/resync` (login-gated)
`ui/app/routes/config_v3_routes.py`: runs `reconcile_models` against the **current applied** models (no staged changes) → add missing + delete extras. **No `fold`, no `config.yaml` write, no restart** — models are hot. Returns `{added, deleted, failed:[{id,op,error}]}`. 422/`{error}` if not hybrid.

### 5.2 Frontend flow (preview + confirm)
On `Models.svelte`, a **"Resync to proxy"** button:
1. `GET /api/config/drift` → render the plan: **"+N add · −K delete"** with the model names (no updates — Resync is presence-only).
2. If the plan is empty → "Already in sync," no-op.
3. If non-empty → **confirm dialog** showing exactly what will be added and **deleted** (deletes emphasized). `confirm()` (works in the non-secure-context UI).
4. On confirm → `POST /api/config/resync` → show the result (`N added, K deleted`, any failures) → re-check drift → badge refreshes.

A minor TOCTOU between preview and execute is acceptable: `resync` recomputes a fresh reconcile and converges regardless; the preview is advisory.

---

## 6. Release plan
- **`1.21.1`** — Piece 1 only (engine patch). Own branch, merged + released + deployed to `.75` first (fixes the live credential-rotation latent bug and ends the constraint-error noise).
- **`1.22.0`** — Pieces 2 + 3 (drift endpoint + badge + Resync), on top of `1.21.1`.

Two branches, two release cycles (semantic-release: `fix:` → 1.21.1, `feat:` → 1.22.0 — kept separate by merging the patch first).

---

## 7. Out of scope
- **Settings drift** (router/litellm/general/credentials) — those render to `config.yaml`, not litellm's model DB; different mechanism.
- **Param-update drift detection** — `/model/info` masks `api_key` and injects defaults, so a reliable param-diff isn't feasible; drift is presence-only (add/delete). Param changes flow through Apply via `changed_ids`.
- **Automatic/periodic background reconcile** — on-demand (Resync) + on-Apply is sufficient; no daemon.

---

## 8. Testing
**Backend (TDD):**
- Piece 1 reconcile tests (§2.4).
- `GET /api/config/drift`: in-sync → `{in_sync:true, missing:[], extra:[]}`; a missing model → listed in `missing_in_litellm`; an extra live model → listed in `extra_in_litellm`; config-only → `{hybrid:false, in_sync:true}`; `/model/info` error → `{error}`. Reuse the `test_config_v3_routes.py` `_client`/`FakeStore` harness + a fake models client returning a chosen live set.
- `POST /api/config/resync`: with a missing model → `add_model` called, `{added:1}`; with an extra → `delete_model` called, `{deleted:1}`; not-hybrid → 422; failures reported not raised.

**Frontend (build + Playwright, LAN-IP):** badge shows "In sync ✓" when converged; after injecting drift (delete a model directly via litellm API, or stage+apply a partial), badge flips to "⚠ N out of sync"; Resync preview lists the plan, confirm executes, badge returns to in-sync. Verified on `http://10.0.20.85:8081`.

**Integration (live-style, local hybrid stack):** reproduce the `name ≠ model_info.id` case → confirm `1.21.1` reconcile converges it with no constraint error; rotate a credential → confirm referencing models are re-pushed.
