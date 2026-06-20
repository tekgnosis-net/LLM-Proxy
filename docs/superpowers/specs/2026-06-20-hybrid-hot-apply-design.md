# LLM-Proxy Admin UI — Hybrid Hot-Apply + Per-Model Health Control + In-Place Usage Refresh (Design)

**Status:** design (brainstormed & approved 2026-06-20). Builds on shipped v3.9.2 (`1.19.2`). Branch(es): `v3.10-hot-apply` (and possibly a split — see §8). Releases as `1.20.0` (quick wins) then `1.21.0` (hybrid centerpiece).

**Why:** today *every* config change — even adding or editing a single model — rewrites `config.yaml` and **restarts the LiteLLM container (~25s)**. Models are the most frequently edited config and the change least deserving of a full restart. LiteLLM exposes a hot model-management API (`/model/new`, `/model/update`, `/model/delete`) that mutates its own DB live — but only when `STORE_MODEL_IN_DB=true`. This batch makes **model add/edit/delete instant** while keeping settings on the (rare) restart path, and folds in two smaller asks the user raised alongside it: **per-model health-check control** (so paid providers aren't billed by background probes) and **in-place Usage auto-refresh** (no scroll-reset).

**Decided in brainstorming (all user-approved):**
- **Keystone:** flip to `STORE_MODEL_IN_DB=true`; `config.yaml` becomes **settings-only** (empty `model_list`); models live in LiteLLM's DB, pushed via API. `ui_config` (the UI's DB) stays the single source of truth; **reproducibility moves from git-tracked YAML to a `ui_config` export/import**.
- **Split-render Apply:** models → hot API; router/litellm/general settings + credentials → `config.yaml` + restart (only when a settings item actually changed); virtual keys (incl. per-key `router_settings`) stay hot via the existing `/key` path.
- **Declarative model reconciliation** (desired-vs-live diff by `model_info.id`), not staged-flag replay — idempotent and self-healing.
- **Credentials — LOCKED (no spike):** API-pushed models carry their key **inline**, resolved server-side from the vault at reconcile time; `config.yaml` in hybrid mode renders **neither `model_list` nor `credential_list`** (fully secret-free). We do **not** depend on LiteLLM resolving `litellm_credential_name` for DB models.
- **Migration — LOCKED (no spike):** **empty-then-fill** — restart with an empty `model_list` first (LiteLLM holds zero models), *then* reconcile pushes every model into the empty DB. A model is never in `config.yaml` and the DB simultaneously, so LiteLLM's dedup-by-id behavior is irrelevant by construction.
- **Health-check:** per-model **disable** via `model_info.disable_background_health_check` + one-time global `general_settings.health_check_skip_disabled_background_models: true`; an editable global `health_check_interval`; an **on-demand "Check now"** reusing `/health/test_connection`.
- **Auto-refresh:** `load(silent)` — auto-refresh ticks skip the `loading=true; summary=null` clear so cells update in place (no unmount → no scroll jump).
- **Phased build:** (1) auto-refresh in-place → (2) per-model health control → (3) split-render Apply engine + export. The first two ship as `1.20.0`; the hybrid as `1.21.0`. The two forks above are **locked deterministically** (not spike-gated); core `STORE_MODEL_IN_DB` behavior is confirmed by the engine's integration tests (§11), not a separate blocking spike.

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
- `config.yaml` in hybrid mode renders **neither `model_list` nor `credential_list`** — only `router_settings`, `litellm_settings`, `general_settings`, and any `passthrough` base. Models live in LiteLLM's DB (pushed via API); model keys are inlined into those API payloads (§5). LiteLLM therefore serves *only* DB models — no config-vs-DB duplication.
- **Reproducibility** no longer lives in git-tracked `config.yaml` (models aren't there anymore). It moves to a **`ui_config` export** (§6): a JSON snapshot of `applied`, which is already the authoritative source.

**Trade-off accepted by the user:** models leave the git-tracked YAML artifact; the export is the new reproducibility artifact. The UI's DB was already the master, so this formalizes what was already true.

**Security — `config.yaml` becomes fully secret-free in hybrid mode.** Because model keys are inlined into `/model/new` (§5) and no `credential_list` is rendered, the only sections left in `config.yaml` are settings, which already use `os.environ/VAR` references for any secret-bearing value (the existing no-literal-secrets guard in `config_store.py`). Provider secrets at rest live in exactly two server-side places: the UI's Fernet vault (canonical) and LiteLLM's own DB (encrypted with `LITELLM_SALT_KEY`, inline in model params). This is a *tightening* of the prior "config.yaml may hold the materialized credential_list" exception.

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
eff = effective(applied, staged)
staged = store.staged()

RESTART_KINDS = {'router_setting', 'litellm_setting', 'general_setting', 'passthrough'}
settings_changed    = any s in staged with s.kind in RESTART_KINDS
creds_changed       = { s.name for s in staged if s.kind == 'credential' }   # NOT a restart trigger in hybrid

# --- Pre-commit (safe to fail; nothing folded) ---
if settings_changed:
    cfg = render_config(eff, decrypt, hybrid=True)  # settings-only: no model_list, no credential_list
    validate_config(cfg)
    write_config_atomic(config_path, dump(cfg))

# --- Commit ---
store.fold()

# --- Post-commit (reported, not rolled back) ---
model_report   = reconcile_models(desired=eff_models_non_deleted, eff=eff,
                                   force_update_for_creds=creds_changed, client=ModelsClient)
restart_report = reload_and_verify([]) if settings_changed else {skipped: true}
return merged status
```

Two key changes:
- **`render_config` gains a `hybrid: bool` parameter** (default `False` for back-compat / non-hybrid). In hybrid mode (`True`): `model_list` is rendered empty **and `credential_list` is omitted** — models (with keys inlined per §5) are applied exclusively via reconciliation, so neither secret-bearing section belongs in the file.
- **`settings_changed` excludes `credential`.** In hybrid mode credentials don't render to `config.yaml`, so a credential edit/rotation must **not** trigger a restart — it triggers re-inlining of affected models on the hot path instead (`creds_changed` → `force_update_for_creds`, §5). The only restart triggers are the four `RESTART_KINDS`.

### 3.3 Declarative model reconciliation (`reconcile_models`)

Idempotent and self-healing — diffs **desired** (UI truth) against **live** (LiteLLM):

```
desired = { it.name (UUID) : render_model_entry(it, resolve_key) for non-deleted model items in eff }
live    = { m.model_info.id : m for m in GET /model/info }   # DB models only (yaml empty)

to_add    = desired.keys - live.keys      → POST /model/new       {model_name, litellm_params, model_info{id,...}}
to_delete = live.keys    - desired.keys   → POST /model/delete    {id}
to_update = {id in both where entry differs} → POST /model/update {id, ...}   (or delete+new if update unsupported)
```

- **Why declarative, not staged-flag replay:** replaying `new/changed/deleted` breaks on partial failure (a re-Apply would `POST /model/new` an already-created id). Diffing desired-vs-live converges no matter the starting state, and **self-heals drift** (e.g. a model someone added directly to LiteLLM gets removed because the UI is the master — the same authority contract the YAML-overwrite already had).
- **Idempotent:** running it when nothing changed is an empty diff (cheap no-op). So a settings-only Apply also *re-verifies* all models are present.
- `render_model_entry(it, resolve_key)` is factored out of the existing `render_config` model branch (DRY — same entry shape, one source) **plus key inlining (§5)**: if `litellm_params.litellm_credential_name` is set, `resolve_key` looks up that `credential` item in `eff`, decrypts its `value_encrypted`, sets `litellm_params.api_key = <plaintext>`, and drops `litellm_credential_name`. `api_key: os.environ/VAR` entries pass through unchanged (LiteLLM resolves the env var at runtime from the litellm container, exactly as for config-loaded models).
- **Drift comparison for `to_update`:** compare the desired entry against the live entry on the **non-secret** fields (`model_name`, `litellm_params` minus `api_key`, `model_info`). `api_key` is excluded because `/model/info` returns it masked — comparing it would force a needless update every cycle.
- **Credential rotation (forced update):** because the masked-`api_key` exclusion means a rotated key wouldn't show as drift, any desired model whose `litellm_credential_name ∈ force_update_for_creds` is **forced into `to_update`** regardless of the non-secret diff — re-inlining the freshly-decrypted key into LiteLLM's DB. This is the hot path; no restart.
- **Missing/deleted credential (fail safe):** if a desired model references a `litellm_credential_name` that has no live `credential` item in `eff` (deleted while still in use, or never created), `resolve_key` cannot produce a key. The model is **skipped and reported** as `failed: {id, op, error: "credential 'X' not found"}` — never pushed with an empty/broken key. Apply stays committed; fixing the credential and re-Applying converges it.

### 3.4 Failure semantics (inherits commit-at-write)

- Pre-commit failure (settings render/validate/write) → `ApplyError`, nothing folded (unchanged today).
- Post-commit reconciliation: each op is tried; failures are **collected and reported** (`models: {added, updated, deleted, failed:[{id, op, error}]}`), `applied` stays committed — identical philosophy to the current restart-failure path (`servant: unhealthy`, `applied: True`). A subsequent Apply (declarative) retries the failed ops automatically.
- The Apply response surfaces both reports so the UI banner can say e.g. *"3 models updated live; settings restart healthy"* or *"2 models live, 1 failed (see detail)"*.

### 3.5 Migration (first hybrid Apply) — LOCKED: empty-then-fill

Flipping `STORE_MODEL_IN_DB=true` requires a LiteLLM restart (env change). The migration is sequenced so **a model is never in `config.yaml` and the LiteLLM DB at the same instant** — which makes LiteLLM's dedup-by-id behavior irrelevant by construction (no surprise, nothing to verify):

1. **Empty the YAML + flip the env, together.** Render `config.yaml` in hybrid mode (empty `model_list`, no `credential_list`) **and** set `STORE_MODEL_IN_DB=true` in compose, then restart. LiteLLM comes up with **zero models** (config empty; DB empty on first run).
2. **Fill the empty DB.** The reconcile runs with `live = {}` → every `ui_config` model is `to_add` → pushed via `/model/new` (keys inlined). `/v1/models` now equals exactly the UI's models.
3. **Steady state.** `config.yaml`'s `model_list` stays empty forever after, so `live` (from `/model/info`) is always DB-only and the desired-vs-live diff is always clean.

**Driven by a one-time, explicit "Enable hot-apply (migrate)" action** in Settings (not an implicit side effect of a normal Apply): it writes the hybrid `config.yaml`, instructs/sets the `STORE_MODEL_IN_DB=true` env + restart, then runs the fill reconcile and reports the result. The env flip is a compose change, so this action documents/guards the one manual step (compose edit) the operator confirms — after which all model edits are hot. Because reconcile is declarative and idempotent, re-running the action is safe.

---

## 4. Per-model health-check control

**Problem:** LiteLLM's background health check sends a **real billed completion** to every model on `health_check_interval`; on paid providers (deepinfra) that's recurring cost for liveness the operator may not want.

**LiteLLM facts:** `health_check_interval` is **global only** (no per-model interval). Per-model **disable** is `model_info.disable_background_health_check: true`, honored only when `general_settings.health_check_skip_disabled_background_models: true` is also set.

**Design (the approved "per-model disable + on-demand"):**
- **Model form** (`Models.svelte`) gains a **Health** control: *Background check: On / Off*. "Off" writes `model_info.disable_background_health_check = true` into the model item's `data.model_info`. Since model items apply via reconciliation (§3.3), the flag rides along **hot** — no restart to toggle it.
- A **one-time global** `general_settings.health_check_skip_disabled_background_models: true` is staged the first time any model is set to "Off" (a `general_setting` item → config.yaml → restart, once). After that, toggles are hot.
- **Global interval** becomes editable in Settings: `general_settings.health_check_interval` (a `general_setting`; restart on change — rare). Lengthening it spaces out checks for the free/local models that stay on. (LiteLLM groups `health_check_interval` and `background_health_checks` under `general_settings`, alongside the skip flag above — confirmed against `config.yaml.example` and the backend round-trip tests.)
- **"Check now"** button per model → reuse `LitellmClient.test_connection(litellm_params)` (LiteLLM `/health/test_connection`, **already wired** in `models_routes.py::/models/test`). On-demand, operator-initiated, so its cost is intentional. Surfaces latency/ok/error inline.

**Guidance baked into the form:** paid providers default the toggle to **Off** (no recurring billing); free/local (vLLM, llama.cpp, Ollama) stay **On**.

---

## 5. Credentials in hybrid mode — LOCKED: inline-resolve, no named-credential dependency

**Decision (deterministic; no spike).** The UI's `credential` vault remains the canonical, Fernet-encrypted source. In hybrid mode it is **not rendered into `config.yaml`**; instead, at reconcile time the UI **resolves and inlines** each model's key:

1. A model item references its credential exactly as today — `litellm_params.litellm_credential_name = "X"` (set in `Models.svelte`).
2. `render_model_entry(it, resolve_key)` looks up `credential` item `X` in `eff`, decrypts `value_encrypted` with the vault Fernet, sets `litellm_params.api_key = <plaintext>`, and **removes** `litellm_credential_name`.
3. The resolved entry is POSTed to `/model/new` (or `/model/update`). LiteLLM stores it in its own DB encrypted with `LITELLM_SALT_KEY`.

**Why this is the locked choice (no surprises):**
- It does **not** depend on LiteLLM resolving `litellm_credential_name` for DB-stored models — a behavior we'd otherwise have to verify and could regress on a LiteLLM upgrade. The key is fully materialized into the payload by us.
- It is the **idiomatic** LiteLLM pattern: with `STORE_MODEL_IN_DB=true`, LiteLLM's own admin UI inlines model keys into its DB; `credential_list` is a config.yaml-era construct.
- `config.yaml` ends up **fully secret-free** (§2) — a security tightening, not a regression.

**Env-var keys** (`api_key: os.environ/VAR`, from the "API key env var" field) pass through unchanged: LiteLLM resolves `os.environ/` at runtime from the litellm container's environment — the same requirement and mechanism as config-loaded models (the var must exist in the litellm container). The UI cannot inline these (it doesn't hold the litellm container's env), and doesn't need to.

**Lifecycle:**
- **Add/edit a model** → key resolved + inlined → `/model/new`/`/model/update` (hot).
- **Rotate a credential** (edit its value) → not a restart; the changed credential name enters `creds_changed` → all models referencing it are force-updated with the new key (§3.3, hot).
- **Delete a credential still in use** → referencing models reported `failed` (§3.3), never pushed keyless.

**Standing constraint reaffirmed:** never rotate `LITELLM_SALT_KEY` (encrypts the LiteLLM-DB model keys) or `SESSION_SECRET`/`credentials_key` (derives the vault Fernet key) after keys are saved — doing so orphans both copies of every secret.

---

## 6. Reproducibility: ui_config export / import

Now that models leave `config.yaml`, the **export is the reproducibility artifact**.

- **Backend:** `GET /api/config/export` → `{ "version": 1, "exported_at": <iso>, "items": [ {kind,name,data} … from applied ] }`. Credentials are exported with `value_encrypted` intact (it's Fernet-encrypted; restoreable only with the same `SESSION_SECRET`/`credentials_key`) — **never plaintext**. Content-Disposition attachment (`ui_config.json`).
- **Import:** already exists (`config_import.py` + `ConfigStore.seed_applied`, bootstrap-only). Add `POST /api/config/import` (admin-gated) that seeds `applied` when empty, or stages a diff when not — exact merge policy: **bootstrap-only for now** (import into an empty `applied`); merge-import is out of scope (§9).
- **Frontend:** a **Settings → "Export config"** button (download) and a documented restore path. This replaces "commit config.yaml to git" as the backup story.

---

## 7. Verification (integration tests — confirmation, not a decision gate)

The two design forks (credentials, migration) are now **locked deterministically** (§5, §3.5), so nothing in the design *waits* on a spike. What remains are the **core `STORE_MODEL_IN_DB` behaviors** — the documented purpose of the feature — which the engine's own integration tests assert during the build (a step we'd run regardless). These tests are the **first task** of the hybrid phase: if any assertion fails, it surfaces *before* the rest of the engine is wired, but the design does not branch on the outcome.

Integration assertions (real local stack, `STORE_MODEL_IN_DB=true`):
1. **Hot add:** `POST /model/new` → `/v1/models` shows it **without a restart**.
2. **Survives restart:** restart container → model still present (DB-persisted).
3. **Empty-config = DB-only:** `config.yaml` with `model_list: []` → `/v1/models` = exactly the DB models (no phantom, no duplication).
4. **Update/delete hot:** `/model/update`, `/model/delete` take effect live.
5. **Reconcile shape:** `GET /model/info` returns `model_info.id` matching what we POST (desired-vs-live keys line up).
6. **Inlined key works:** a model pushed with an inlined `api_key` (resolved from the vault, §5) completes a `/health/test_connection` against a real backend.

(Migration dedup and named-credential resolution are *not* listed — the locked designs make them moot.)

---

## 8. Build phasing

**Ship `1.20.0` (quick wins, no keystone risk — own branch, mergeable independently):**
1. **Auto-refresh in-place** — `Usage.svelte`: `load(silent=false)`; auto-refresh tick calls `load(true)` which skips `loading=true; summary=null; recent=[]`, updating cells in place (no unmount → no scroll reset). Initial/range-change loads keep the spinner. (§ small, frontend-only.)
2. **Per-model health control** (§4) — model-form Health toggle (`disable_background_health_check`), one-time global skip flag, editable `health_check_interval`, "Check now" (reuse `test_connection`).

**Ship `1.21.0` (hybrid centerpiece — forks locked, no decision gate):**
3. **Core-behavior integration tests** (§7) — first task of the phase: assert `STORE_MODEL_IN_DB=true` hot-add / survive-restart / empty=DB-only / update-delete-hot / id-match / inlined-key against a real local stack. Confirmation before wiring the rest; the design doesn't branch on it.
4. **Split-render Apply engine** (§3): `render_config(hybrid=…)`, `render_model_entry(it, resolve_key)` extraction (with key inlining + `litellm_credential_name` removal), `ModelsClient` (mirrors `KeysClient`: `/model/new|update|delete`, `/model/info`), `reconcile_models` (declarative diff + forced-update-on-cred-change + missing-cred reporting), rewired `apply_config` (settings/creds split, merged report), `STORE_MODEL_IN_DB=true` in compose, hybrid render (no `model_list`, no `credential_list`), one-time **"Enable hot-apply (migrate)"** action (§3.5).
5. **Reproducibility export/import** (§6).
6. **Integration + release** — Playwright on **`http://10.0.20.85:8081`** (LAN-IP): add a model → appears live with **no restart**; edit a setting → restart path still works; rotate a credential → referencing models re-inlined hot; health toggle hot; "Check now" returns; export downloads; survives a container restart.

Whether step 4–5 is one plan or two is a writing-plans decision; this spec is one design.

---

## 9. Out of scope

- **Per-model health *interval*** (LiteLLM has no such knob — disable + on-demand is the supported model).
- **Merge-import** of a `ui_config` export into a non-empty `applied` (bootstrap-only import for now).
- **Hot apply of settings** (router/litellm/general/passthrough) — these genuinely need a restart; no LiteLLM hot path exists. (Credentials are the exception: in hybrid mode they apply hot via model re-inlining — §5.) Per-key `router_settings` remain the hot lever for per-consumer routing.
- **Rollback** of partial model reconciliation (commit-at-write + declarative retry is the chosen model, consistent with today's restart path).
- **Removing `config.yaml` entirely** — settings still live there; only `model_list` empties.

---

## 10. Per-key routing as the first lever (docs nudge, near-zero code)

Virtual keys already carry `router_settings` and apply hot via `/key`. Add a one-line note on the Routing screen: *"For per-consumer routing, set it on the virtual key (applies instantly). Changes here are global defaults and require a restart."* Makes the already-hot path discoverable; no new mechanism.

---

## 11. Testing

**Backend (TDD, pure where possible):**
- `render_config(hybrid=True)` → `model_list == []` **and `credential_list` absent**; settings sections unchanged. `render_config(hybrid=False)` (default) unchanged from today (golden test for back-compat).
- `render_model_entry(it, resolve_key)`: produces the same base entry shape as the old inline branch **plus** key inlining — `litellm_credential_name` resolved to `api_key` and removed; `api_key: os.environ/VAR` passes through; missing credential raises the "credential not found" path.
- `reconcile_models` diff logic: pure function over (desired dict, live list, `force_update_for_creds`) → `{to_add, to_update, to_delete, failed}`; covers add-only, delete-only, update-on-param-change, no-op, id-stable, **forced-update on rotated credential** (non-secret diff empty but cred changed), and **missing-credential → failed** (not pushed). Client calls mocked via injected transport, mirroring the existing `Reloader`/`KeysClient` transport-injection test pattern.
- `apply_config` split: `settings_changed=False` (incl. credential-only change) → no file write, no restart, reconcile runs; `settings_changed=True` → file written + restart + reconcile; credential-only change → reconcile force-updates affected models, **no restart**; fold still the commit boundary; reconcile failures reported not raised.
- Export endpoint shape; credentials exported encrypted (never plaintext); import bootstraps empty `applied` only.
- Health toggle: model item with `disable_background_health_check` round-trips through reconcile; "Check now" delegates to `test_connection`.

**Core-behavior integration (real local stack, first task of the hybrid phase):** the six assertions in §7.

**Frontend (Playwright, LAN-IP `http://10.0.20.85:8081`):**
- Usage auto-refresh: scroll down, wait for a tick, assert scroll position **unchanged** and a cell value updated in place.
- Models: add a model → appears in `/v1/models` with **no restart** (assert no ~25s stall / container uptime unchanged); Health toggle persists; "Check now" shows a result.
- Settings change → restart path still healthy.
- Export downloads a valid `ui_config.json`.

**Integration (host-style, local):** add/edit/delete model hot; settings-only change restarts; model survives a container restart; declarative reconcile heals an injected drift.
