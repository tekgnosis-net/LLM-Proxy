# LLM-Proxy Admin UI — Hybrid Hot-Apply + Per-Model Health Control + In-Place Usage Refresh (Design)

**Status:** design (brainstormed & approved 2026-06-20). Builds on shipped v3.9.2 (`1.19.2`). Branch(es): `v3.10-hot-apply` (and possibly a split — see §8). Releases as `1.20.0` (quick wins) then `1.21.0` (hybrid centerpiece).

**Why:** today *every* config change — even adding or editing a single model — rewrites `config.yaml` and **restarts the LiteLLM container (~25s)**. Models are the most frequently edited config and the change least deserving of a full restart. LiteLLM exposes a hot model-management API (`/model/new`, `/model/update`, `/model/delete`) that mutates its own DB live — but only when `STORE_MODEL_IN_DB=true`. This batch makes **model add/edit/delete instant** while keeping settings on the (rare) restart path, and folds in two smaller asks the user raised alongside it: **per-model health-check control** (so paid providers aren't billed by background probes) and **in-place Usage auto-refresh** (no scroll-reset).

**Decided in brainstorming (all user-approved):**
- **Keystone:** flip to `STORE_MODEL_IN_DB=true`; `config.yaml` becomes **settings-only** (empty `model_list`); models live in LiteLLM's DB, pushed via API. `ui_config` (the UI's DB) stays the single source of truth; **reproducibility moves from git-tracked YAML to a `ui_config` export/import**.
- **Split-render Apply:** models → hot API; router/litellm/general settings + credentials → `config.yaml` + restart (only when a settings item actually changed); virtual keys (incl. per-key `router_settings`) stay hot via the existing `/key` path.
- **Declarative model reconciliation** (desired-vs-live diff by `model_info.id`), not staged-flag replay — idempotent and self-healing.
- **Health-check:** per-model **disable** via `model_info.disable_background_health_check` + one-time global `general_settings.health_check_skip_disabled_background_models: true`; an editable global `health_check_interval`; an **on-demand "Check now"** reusing `/health/test_connection`.
- **Auto-refresh:** `load(silent)` — auto-refresh ticks skip the `loading=true; summary=null` clear so cells update in place (no unmount → no scroll jump).
- **Phased build:** (1) auto-refresh in-place → (2) per-model health control → (3) **STORE_MODEL_IN_DB verification spike** → (4) split-render Apply engine + export. The first two ship as `1.20.0`; the hybrid as `1.21.0`.

---

## 1. Current architecture (what we're extending)

The Apply pipeline (`ui/app/config_engine.py::apply_config`) is **commit-at-write**:

1. `effective(applied, staged)` → items each tagged `flag ∈ {None, new, changed, deleted}` (`config_render.py`).
2. `render_config(eff, decrypt)` → a full `config.yaml` dict (models → `model_list`, credentials → `credential_list`, router/litellm/general → their sections). `model_info.id` defaults to the item's `name` (a UUID).
3. `validate_config` (pre-commit).
4. `write_config_atomic` (pre-commit; atomic + readback).
5. `store.fold()` — **the commit boundary** (staged → applied, clear staged; transactional).
6. `reloader.reload_and_verify(expected_models)` — **post-commit, reported, never rolled back**: restart container, poll `/health/readiness` + `/v1/models` until the expected models appear or timeout → `servant: healthy|unhealthy`.

Hot precedent already in the codebase: **virtual keys** use `KeysClient` (`/key/generate|update|delete`) — no restart, no `config.yaml`. The hybrid generalizes that precedent to models.

---

## 2. Keystone: STORE_MODEL_IN_DB=true, settings-only config.yaml

**Pivot:** set `STORE_MODEL_IN_DB=true` on the LiteLLM container. Consequences:

- LiteLLM persists models in **its own Postgres tables** (`LiteLLM_ProxyModelTable`), in the same Postgres instance the stack already runs. These **survive container restarts** (DB-backed, not in-memory).
- `config.yaml` `model_list` is rendered **empty** in hybrid mode → LiteLLM serves *only* DB models (no config-vs-DB duplication). All other sections (`router_settings`, `litellm_settings`, `general_settings`, `credential_list`) stay in `config.yaml`.
- **Reproducibility** no longer lives in git-tracked `config.yaml` (models aren't there anymore). It moves to a **`ui_config` export** (§6): a JSON snapshot of `applied`, which is already the authoritative source.

**Trade-off accepted by the user:** models leave the git-tracked YAML artifact; the export is the new reproducibility artifact. The UI's DB was already the master, so this formalizes what was already true.

**Security:** credentials remain in `config.yaml` `credential_list` (the materialized vault, 0600/gitignored) — see §5. Models pushed via API reference credentials **by name** (`litellm_credential_name`), so no decrypted secret travels in the `/model/new` body in the primary design. (Inline-key fallback in §5 only if the spike disproves named-credential resolution.)

---

## 3. Split-render Apply engine

### 3.1 The boundary

| Item kind | Apply path | Restart? |
|---|---|---|
| `model` (add/edit/delete) | `/model/new` · `/model/update` · `/model/delete` (declarative reconcile) | **hot** |
| `router_setting`, `litellm_setting`, `general_setting`, `credential`, `passthrough` | `config.yaml` + restart | yes (only if one changed) |
| virtual keys (separate subsystem) | `/key/generate|update|delete` (existing) | hot |

### 3.2 New `apply_config` flow (replaces the single render+restart)

```
eff      = effective(applied, staged)
settings_changed = any staged item with kind != 'model'   # from store.staged()

# --- Pre-commit (safe to fail; nothing folded) ---
if settings_changed:
    cfg = render_config(eff, decrypt, models_in_yaml=False)  # settings-only, model_list=[]
    validate_config(cfg)
    write_config_atomic(config_path, dump(cfg))

# --- Commit ---
store.fold()

# --- Post-commit (reported, not rolled back) ---
model_report = reconcile_models(desired=eff_models_non_deleted, client=ModelsClient)
restart_report = reload_and_verify([]) if settings_changed else {skipped: true}
return merged status
```

Key change: **`render_config` gains a `models_in_yaml: bool` parameter** (default `True` for back-compat / non-hybrid). In hybrid mode the caller passes `False` → `model_list` rendered empty; models are applied exclusively via reconciliation.

### 3.3 Declarative model reconciliation (`reconcile_models`)

Idempotent and self-healing — diffs **desired** (UI truth) against **live** (LiteLLM):

```
desired = { it.name (UUID) : render_model_entry(it) for non-deleted model items in eff }
live    = { m.model_info.id : m for m in GET /model/info }   # DB models only (yaml empty)

to_add    = desired.keys - live.keys      → POST /model/new       {model_name, litellm_params, model_info{id,...}}
to_delete = live.keys    - desired.keys   → POST /model/delete    {id}
to_update = {id in both where entry differs} → POST /model/update {id, ...}   (or delete+new if update unsupported)
```

- **Why declarative, not staged-flag replay:** replaying `new/changed/deleted` breaks on partial failure (a re-Apply would `POST /model/new` an already-created id). Diffing desired-vs-live converges no matter the starting state, and **self-heals drift** (e.g. a model someone added directly to LiteLLM gets removed because the UI is the master — the same authority contract the YAML-overwrite already had).
- **Idempotent:** running it when nothing changed is an empty diff (cheap no-op). So a settings-only Apply also *re-verifies* all models are present.
- `render_model_entry` is factored out of the existing `render_config` model branch (DRY — same shape, one source).

### 3.4 Failure semantics (inherits commit-at-write)

- Pre-commit failure (settings render/validate/write) → `ApplyError`, nothing folded (unchanged today).
- Post-commit reconciliation: each op is tried; failures are **collected and reported** (`models: {added, updated, deleted, failed:[{id, op, error}]}`), `applied` stays committed — identical philosophy to the current restart-failure path (`servant: unhealthy`, `applied: True`). A subsequent Apply (declarative) retries the failed ops automatically.
- The Apply response surfaces both reports so the UI banner can say e.g. *"3 models updated live; settings restart healthy"* or *"2 models live, 1 failed (see detail)"*.

### 3.5 Migration (first hybrid Apply) — **spike-gated, see §7**

Flipping `STORE_MODEL_IN_DB=true` requires a LiteLLM restart (env change). The migration sequencing the spike must confirm:
1. Restart LiteLLM with `STORE_MODEL_IN_DB=true` **and** a `config.yaml` whose `model_list` is already empty (so it starts with zero models).
2. First reconcile pushes all `ui_config` models into the LiteLLM DB via `/model/new` (live becomes desired).
3. Verify `/v1/models` = exactly the UI's models, and they **survive a restart**.

A one-time "Migrate to hot-apply" action (or an automatic detect-and-migrate on first hybrid Apply) drives this. Exact UX decided after the spike confirms LiteLLM's dedup-by-id behavior.

---

## 4. Per-model health-check control

**Problem:** LiteLLM's background health check sends a **real billed completion** to every model on `health_check_interval`; on paid providers (deepinfra) that's recurring cost for liveness the operator may not want.

**LiteLLM facts:** `health_check_interval` is **global only** (no per-model interval). Per-model **disable** is `model_info.disable_background_health_check: true`, honored only when `general_settings.health_check_skip_disabled_background_models: true` is also set.

**Design (the approved "per-model disable + on-demand"):**
- **Model form** (`Models.svelte`) gains a **Health** control: *Background check: On / Off*. "Off" writes `model_info.disable_background_health_check = true` into the model item's `data.model_info`. Since model items apply via reconciliation (§3.3), the flag rides along **hot** — no restart to toggle it.
- A **one-time global** `general_settings.health_check_skip_disabled_background_models: true` is staged the first time any model is set to "Off" (a `general_setting` item → config.yaml → restart, once). After that, toggles are hot.
- **Global interval** becomes editable in Settings: `litellm_settings.health_check_interval` (a `litellm_setting`; restart on change — rare). Lengthening it spaces out checks for the free/local models that stay on.
- **"Check now"** button per model → reuse `LitellmClient.test_connection(litellm_params)` (LiteLLM `/health/test_connection`, **already wired** in `models_routes.py::/models/test`). On-demand, operator-initiated, so its cost is intentional. Surfaces latency/ok/error inline.

**Guidance baked into the form:** paid providers default the toggle to **Off** (no recurring billing); free/local (vLLM, llama.cpp, Ollama) stay **On**.

---

## 5. Credentials in hybrid mode

**Primary design (least-change):** credentials stay `config.yaml` `credential_list` items (kind `credential` → settings → restart only when a credential is added/rotated, which is rare). API-pushed models reference them by **`litellm_credential_name`**; LiteLLM resolves the named credential from its config-loaded `credential_list`.

**Spike must confirm:** a model added via `/model/new` with `litellm_params.litellm_credential_name = "X"` correctly resolves credential `X` from the config-loaded `credential_list`. 

**Fallback (if named resolution fails for API models):** pass the decrypted `api_key` inline in the `/model/new` `litellm_params`. LiteLLM stores it in its DB encrypted with `LITELLM_SALT_KEY`. Exposure is equivalent to writing it into `config.yaml` (server-side, internal docker network only) — acceptable but second choice because it duplicates the secret into LiteLLM's DB. **Never** rotate `LITELLM_SALT_KEY`/`SESSION_SECRET` after keys are saved (standing constraint).

---

## 6. Reproducibility: ui_config export / import

Now that models leave `config.yaml`, the **export is the reproducibility artifact**.

- **Backend:** `GET /api/config/export` → `{ "version": 1, "exported_at": <iso>, "items": [ {kind,name,data} … from applied ] }`. Credentials are exported with `value_encrypted` intact (it's Fernet-encrypted; restoreable only with the same `SESSION_SECRET`/`credentials_key`) — **never plaintext**. Content-Disposition attachment (`ui_config.json`).
- **Import:** already exists (`config_import.py` + `ConfigStore.seed_applied`, bootstrap-only). Add `POST /api/config/import` (admin-gated) that seeds `applied` when empty, or stages a diff when not — exact merge policy: **bootstrap-only for now** (import into an empty `applied`); merge-import is out of scope (§9).
- **Frontend:** a **Settings → "Export config"** button (download) and a documented restore path. This replaces "commit config.yaml to git" as the backup story.

---

## 7. Verification spike (gate before §3 build)

Before building the engine, verify on a **throwaway local stack** that `STORE_MODEL_IN_DB=true` behaves exactly as designed. **Findings update the spec/plan before any engine code is written.**

1. **Hot add:** `POST /model/new` → `/v1/models` shows it **without restart**.
2. **Survives restart:** restart container → model still present (DB-persisted).
3. **Empty-config = DB-only:** `config.yaml` with `model_list: []` + DB models → `/v1/models` = exactly the DB models (no phantom, no duplication).
4. **Update/delete hot:** `/model/update`, `/model/delete` take effect live.
5. **Reconcile shape:** `GET /model/info` returns `model_info.id` matching what we POST (so desired-vs-live keys line up).
6. **Named credential resolution (§5):** a model added via API with `litellm_credential_name` resolves the config-loaded credential (decides primary vs fallback).
7. **Dedup-by-id on migration (§3.5):** pushing a model whose id also appears in `config.yaml` — does LiteLLM dedup or duplicate? (Decides migration sequencing.)

Output: a short findings note appended here; the plan's engine tasks reference confirmed behavior, not assumptions.

---

## 8. Build phasing

**Ship `1.20.0` (quick wins, no keystone risk — own branch, mergeable independently):**
1. **Auto-refresh in-place** — `Usage.svelte`: `load(silent=false)`; auto-refresh tick calls `load(true)` which skips `loading=true; summary=null; recent=[]`, updating cells in place (no unmount → no scroll reset). Initial/range-change loads keep the spinner. (§ small, frontend-only.)
2. **Per-model health control** (§4) — model-form Health toggle (`disable_background_health_check`), one-time global skip flag, editable `health_check_interval`, "Check now" (reuse `test_connection`).

**Ship `1.21.0` (hybrid centerpiece — gated by the spike):**
3. **Spike** (§7) — verify, record findings.
4. **Split-render Apply engine** (§3): `render_config(models_in_yaml=…)`, `render_model_entry` extraction, `ModelsClient` (mirrors `KeysClient`), `reconcile_models`, rewired `apply_config`, merged Apply report, `STORE_MODEL_IN_DB=true` in compose + empty-model_list render, migration action (§3.5).
5. **Reproducibility export/import** (§6).
6. **Integration + release** — Playwright on **`http://10.0.20.85:8081`** (LAN-IP): add a model → appears live with **no restart**; edit a setting → restart path still works; health toggle hot; "Check now" returns; export downloads; survives a container restart.

Whether step 4–5 is one plan or two is a writing-plans decision; this spec is one design.

---

## 9. Out of scope

- **Per-model health *interval*** (LiteLLM has no such knob — disable + on-demand is the supported model).
- **Merge-import** of a `ui_config` export into a non-empty `applied` (bootstrap-only import for now).
- **Hot apply of settings** (router/litellm/general/credentials) — these genuinely need a restart; no LiteLLM hot path exists. Per-key `router_settings` remain the hot lever for per-consumer routing.
- **Rollback** of partial model reconciliation (commit-at-write + declarative retry is the chosen model, consistent with today's restart path).
- **Removing `config.yaml` entirely** — settings still live there; only `model_list` empties.

---

## 10. Per-key routing as the first lever (docs nudge, near-zero code)

Virtual keys already carry `router_settings` and apply hot via `/key`. Add a one-line note on the Routing screen: *"For per-consumer routing, set it on the virtual key (applies instantly). Changes here are global defaults and require a restart."* Makes the already-hot path discoverable; no new mechanism.

---

## 11. Testing

**Backend (TDD, pure where possible):**
- `render_config(models_in_yaml=False)` → `model_list == []`, other sections unchanged; `render_model_entry` produces the same entry shape the old inline branch did (golden test).
- `reconcile_models` diff logic: pure function over (desired dict, live list) → `{to_add, to_update, to_delete}`; covers add-only, delete-only, update-on-param-change, no-op, and id-stable cases. (Client calls mocked via injected transport, mirroring the existing `Reloader`/`KeysClient` transport-injection test pattern.)
- `apply_config` split: settings_changed=False → no file write, no restart, reconcile runs; settings_changed=True → file written + restart + reconcile; fold still the commit boundary; reconcile failures reported not raised.
- Export endpoint shape; credentials exported encrypted (never plaintext); import bootstraps empty `applied` only.
- Health toggle: model item with `disable_background_health_check` round-trips through reconcile; "Check now" delegates to `test_connection`.

**Spike (integration, throwaway stack):** the seven checks in §7 — recorded before engine code.

**Frontend (Playwright, LAN-IP `http://10.0.20.85:8081`):**
- Usage auto-refresh: scroll down, wait for a tick, assert scroll position **unchanged** and a cell value updated in place.
- Models: add a model → appears in `/v1/models` with **no restart** (assert no ~25s stall / container uptime unchanged); Health toggle persists; "Check now" shows a result.
- Settings change → restart path still healthy.
- Export downloads a valid `ui_config.json`.

**Integration (host-style, local):** add/edit/delete model hot; settings-only change restarts; model survives a container restart; declarative reconcile heals an injected drift.
