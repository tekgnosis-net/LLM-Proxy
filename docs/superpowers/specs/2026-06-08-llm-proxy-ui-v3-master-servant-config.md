# LLM-Proxy Admin UI — v3 Design: Master/Servant Config Staging

A foundational redesign of how configuration is owned, staged, applied, and
discarded. It supersedes the v2.1 file-diff apply-model and the v2.2 credential
materialization, unifying *all* config under one explicit staging model.

> Supersedes the apply/discard internals of
> [v2](2026-06-07-llm-proxy-ui-v2-design.md) and
> [v2.4](2026-06-08-llm-proxy-ui-v2.4-design.md). The v2.4 read-only fix and the
> catalog-driven provider picker carry forward unchanged; v2.4's `/api/discard`
> and the `.applied.yaml` baseline are replaced by this model.

## The principle: Master / Servant

- **Master = the UI app + its database.** It owns *intent* — the desired
  configuration. The database is the single source of truth.
- **Servant = LiteLLM.** It owns *execution* — it serves whatever the Master
  dispatched. It stays in config-only mode (`store_model_in_db: false`).
- **`config.yaml` is neither the truth nor a store — it is the instruction sheet
  the Master renders and hands the Servant**, rewritten on every Apply.

Every behavior follows from ownership: the file can't be authoritative (the Servant
doesn't own its orders); a hand-edit to the file is a scribble the Master overwrites
on the next dispatch; "pending" is the Master's set of un-dispatched intent;
"passthrough" is the part of the order the Master writes free-form (YAML-validated so
the Servant doesn't choke).

## Goals
1. The **DB is authoritative** for all `config.yaml`-rendered settings; `config.yaml`
   is a rendered artifact.
2. An **explicit staged-changes model** with per-item flags (`new`/`changed`/`deleted`),
   surviving logout/restart, driving both the render and rich UI feedback.
3. **Apply** = render → validate → write → read-back (the commit point) → fold staged
   into applied → clear staged → restart → verify (restart result reported, never
   rolled back). **Discard** = clear staged (no file write, no restart).
4. A **passthrough** mechanism: a UI raw-YAML editor (DB-stored) for advanced LiteLLM
   keys the UI doesn't model, merged into the render and YAML-validated.
5. Credentials become *just another staged item kind* — eliminating the v2.2
   vault-vs-config inconsistency (a discarded credential add truly disappears).

## Non-goals
- **Not** in scope (stays on LiteLLM's runtime API/DB — the Servant's operational
  state, created live with no restart): **virtual keys, budgets, spend, housekeeping.**
  These are a different axis and are not part of the staged/persisted config tables.
- No change to LiteLLM's config-only mode (it still reads the rendered file).
- No file-as-source-of-truth and no file→DB reconciliation (passthrough is option #1:
  UI-entered, DB-stored; the file is purely a render target).

## Scope (what the Master compiles)
The staging model governs exactly the `config.yaml` sections: `model_list`,
`router_settings`, `litellm_settings` (incl. caching), `general_settings`,
`credential_list`, and the **passthrough** (any other top-level keys). Everything
else LiteLLM does at runtime (keys/spend/etc.) is out of scope.

---

## Architecture

### Data model (two tables + a unifying item shape)
A config "item" is the unit of staging. Items are typed by `kind`:

| kind | name (identifier) | data (JSON) |
|---|---|---|
| `model` | model_name | `{litellm_params, model_info}` |
| `credential` | credential_name | `{provider, value_encrypted}` (Fernet) |
| `router_setting` | the key (e.g. `routing_strategy`) | the value |
| `litellm_setting` | the key (e.g. `cache`, `cache_params`) | the value |
| `general_setting` | the key (e.g. `background_health_checks`) | the value |
| `passthrough` | `_` (singleton) | the raw extra-YAML dict |

Two tables (asyncpg / Postgres, `ui_` prefix):
```
ui_config_applied(kind text, name text, data jsonb, updated_at timestamptz,
                  PRIMARY KEY(kind, name))      -- the last-APPLIED state == what config.yaml holds
ui_config_staged (kind text, name text, data jsonb, flag text, updated_at timestamptz,
                  PRIMARY KEY(kind, name))      -- pending; flag ∈ 'new'|'changed'|'deleted'
```
- **Applied** = the dispatched truth (mirrors the live `config.yaml`).
- **Staged** = pending intent. `new` = item absent from applied; `changed` = item
  exists in applied, new data; `deleted` = item exists in applied, marked for removal
  (the applied row stays until Apply — that's why deletes "remain in DB").
- Credential secrets are **encrypted at rest** in `data.value_encrypted` (Fernet, key
  from `SESSION_SECRET`/`CREDENTIALS_KEY` — carried from v2.2).

### The effective view (what the UI renders)
`effective = applied`, then overlay staged: `new`→add, `changed`→replace,
`deleted`→keep but mark struck-through. Each item carries its `flag` (or none =
clean) so the UI colors `new`/`changed` and strikes `deleted`.

### The render (effective → `config.yaml`)
`render(applied ⊕ staged ⊕ passthrough)`:
1. Group non-deleted effective items by kind → assemble sections: all `model` →
   `model_list`; `router_setting` keys → `router_settings` dict; `litellm_setting` →
   `litellm_settings`; `general_setting` → `general_settings`; `credential` →
   `credential_list` with **decrypted** `api_key` (materialized, as v2.2).
2. Deep-merge the **passthrough** dict for any top-level keys the Master added
   free-form (passthrough never overrides a UI-managed section — managed wins).
3. **Validate** the assembled dict through the existing guardrails + schema
   (`config_store.validate_config`): routing-strategy enum, no `ssl` in cache_params,
   no literal secrets *except* the materialized `credential_list`, required model
   fields. This is the Master's "don't crash the Servant" check.
4. Serialize to YAML.

### Save / Apply / Discard
- **Save (per item, per screen):** upsert into `ui_config_staged` with the right flag
  (compare to applied to decide `new` vs `changed`; a UI delete writes flag `deleted`).
  No file write, no restart. Returns the new pending count.
- **Apply — the commit boundary is a successful, read-back file write; there is NO
  rollback after it.** Reverting a written file would desync it from the DB (file says
  X, DB says Y, staged empty so nothing flags the drift). Exact sequence:
  1. `render(effective)` → **validate** (guardrails + schema). Invalid → **422**,
     nothing written, **staged intact** (abortable).
  2. **Backup** the current file (`config.yaml.bak.*`, 0600) for inspection.
  3. **Write** rendered YAML to a temp file, then **read it back + re-parse** to confirm
     the bytes are on disk and valid. Disk/readback failure → **500**, nothing folded,
     **staged intact** (abortable).
  4. **COMMIT:** `os.replace` temp → `config.yaml`; `chmod 0600`; **fold** staged into
     applied (apply `new`/`changed`, delete `deleted` rows from applied); **clear**
     staged. The invariant now holds: `config.yaml == render(applied)`, staged empty.
  5. **Restart** the Servant; **verify** health + `/v1/models`.
  6. The restart/verify result is **reported, not rolled back**: healthy →
     `{applied:true, servant:"healthy"}`; unhealthy → `{applied:true,
     servant:"unhealthy", detail}` — a **warning**, not a failure. The config is
     committed and consistent; a valid config the Servant still rejects at runtime is an
     operational issue the Master **fixes forward** (correct the setting in the UI and
     re-apply). **No auto-revert, no last-good snapshot** — by design, to keep
     `file == DB` always true. The backup file is retained for inspection.
- **Discard:** `DELETE FROM ui_config_staged` (optionally scoped to one item). No file
  write, no restart. Pending → empty.

### Pending / deviation
- **Pending = `ui_config_staged` is non-empty.** Survives logout/restart (DB-backed).
- **Drift guard:** if staged is empty but `render(applied)` ≠ the on-disk file (someone
  hand-edited the Servant's copy), surface a non-blocking notice with "Re-apply from
  Master" (overwrites the file from the DB) — the file is never authoritative.

### Bootstrap & migration
- **First run (applied empty):** import the existing `config.yaml` (or seed from
  `config.yaml.example`): parse → split known sections into typed items, all other
  top-level keys → the `passthrough` item; encrypt any literal `credential_list`
  secrets into `credential` items. `config.yaml` already matches, so no rewrite.
- **v2 → v3 upgrade:** same import runs once; a v2 deployment's `config.yaml` (with a
  materialized `credential_list`) imports cleanly (literals → encrypted credential
  items). The v2.2 `ui_credentials` table, if present, is migrated into `credential`
  items then dropped. Idempotent (guarded by an applied-table-empty check + a
  migration marker).

---

## API
- `GET /api/config/state` → the effective view: items grouped by kind, each with its
  `flag`, **credential values redacted** (`***`); plus `pending` (bool) + counts.
- `PUT /api/config/item` → stage one item `{kind, name, data}` (backend computes
  new/changed); `DELETE /api/config/item/{kind}/{name}` → stage a `deleted`.
- `GET /api/config/passthrough` (redacted) / `PUT /api/config/passthrough` (raw YAML →
  parsed, staged).
- `POST /api/apply` → render+validate+write+readback+fold+clear+restart+verify →
  **200** `{applied:true, servant:"healthy"|"unhealthy", detail?}` / **422** invalid
  render (nothing written) / **500** write/readback failed (nothing folded). No 409 —
  there is no post-write rollback.
- `POST /api/discard` (optional `?kind=&name=` to discard one) → clear staged.
- `GET /api/config/rendered` → the would-be `config.yaml` (redacted) — a "preview the
  dispatch" view. All login-gated; master key + secrets stay server-side.

## Frontend
- Every config screen reads `GET /api/config/state` and writes via `PUT/DELETE
  /api/config/item`. Items show their flag: `new`/`changed` in an accent color,
  `deleted` red + strikethrough (still listed until Apply/Discard).
- The global bar (when pending) shows **Apply** + **Discard** with the count; Discard
  confirms. The bar reflects DB-backed pending, so it's correct after a fresh login.
- New **Raw / Advanced (passthrough)** editor (Settings): a YAML textarea (validated)
  for keys the UI doesn't model.
- Carried from v2.4: the read-only Provider-Keys fix and the catalog-driven provider
  picker (now writing `credential`/`model` items into the staged table).
- A **"Preview rendered config.yaml"** view (from `/api/config/rendered`, redacted)
  replaces the old read-only config viewer.

## Validation / guardrails (the Servant-safety contract)
All v1/v2 guardrails move into the render's validate step: routing-strategy enum, no
`ssl` in cache_params, required model fields, no literal secrets except the
materialized `credential_list`. **The passthrough is YAML-parsed + run through the same
validate**, so a bad free-form key is caught before it can crash/silently-break the
Servant. Apply never writes a file that fails validation.

## Error handling
- Stage with invalid data → 422, nothing staged.
- Apply — split by the commit boundary:
  - **Pre-commit (abortable, staged intact):** invalid render → **422** (nothing
    written); write/readback/disk error → **500** (nothing folded).
  - **Post-commit (no rollback):** once the file is written+folded, Apply has
    succeeded. A failed Servant restart/verify returns **200** with
    `servant:"unhealthy"` + detail — a warning, not a failure; the operator fixes
    forward. The file and DB stay consistent regardless.
- Passthrough YAML parse/validate error → 422 with the parse message.
- All read endpoints redact credential values.

## Security
- Credential secrets encrypted at rest in `ui_config_*.data` (Fernet); never returned
  un-redacted. Rendered `config.yaml` is `0600` + gitignored (it now holds materialized
  secrets) with the committed `config.yaml.example` bootstrap — carried from v2.2.
- The DB is the secret-bearing store; the file is a rendered, owner-only artifact.

## Disposition of in-flight v2.4 (folded in)
- **Keep, unchanged:** the read-only Provider-Keys save fix; the catalog-driven
  provider picker (+ `endpoints_to_modes`, the fallback list, special-field map).
- **Rebuilt on this model:** Discard, the apply pipeline, pending detection — the
  v2.4 `/api/discard` + `.applied.yaml` baseline are removed in favor of the
  staged-table model. The v2.4 commits ship as part of the v3 release (nothing pushed
  separately).

## Testing
- Backend TDD: the item/effective/render functions (pure), the applied/staged store
  (CRUD + fold + clear), apply (pre-commit invalid/disk-error → staged intact; commit
  folds+clears; post-commit restart-fail → reported `unhealthy`, NOT rolled back, file==DB),
  bootstrap-import (config.yaml → items, incl. credential encryption + passthrough),
  validation incl. passthrough, redaction.
- Real-stack integration: stage across several screens → one Apply (one restart) →
  applied; stage a delete → strikethrough → Discard restores it; passthrough key
  renders + validates; the user's original sequence (add key, discard, no reappear);
  apply-failure rolls back and keeps staged; bootstrap import on a fresh + a v2 config.

## Risks
- **Big refactor:** the whole config subsystem (backend engine + every config screen).
  Mitigate with strict phase decomposition + per-phase real-stack verification.
- **Migration correctness:** importing an existing/v2 `config.yaml` must be lossless
  (managed → items, everything else → passthrough). Test against real configs; the
  drift guard catches mismatches.
- **Render fidelity:** the assembled YAML must equal what LiteLLM expects — covered by
  the validate step + the apply health/`/v1/models` verify.

## Decomposition (one spec, sequential plans)
This is one subsystem built in layers, each independently testable:
- **v3.1 — engine + migration (backend):** schema, item/effective/render, applied/staged
  store, apply/discard, bootstrap-import, validation. CLI/API-testable without the UI.
- **v3.2 — config API:** the `/api/config/*` endpoints over the engine (replacing the
  v2 config routes).
- **v3.3 — frontend rewiring:** every config screen → state/item endpoints + flag
  rendering + Apply/Discard bar + passthrough editor + rendered-preview.
Each is its own plan; built subagent-driven; v3 ships (and pushes) as one release.
