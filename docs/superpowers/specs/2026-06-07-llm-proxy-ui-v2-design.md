# LLM-Proxy Admin UI — v2 Design

Refinement pass after the v1 build (Phases 1–5). v1 shipped functional but
plainer than the agreed [prototype](2026-06-07-llm-proxy-ui-prototype.html) — most
visibly the Dashboard, which shipped as a Phase-1 stub and was never enriched.
v2 closes that gap and adds the workflow/feature depth below.

> Builds on the v1 spec ([`2026-06-07-llm-proxy-ui-design.md`](2026-06-07-llm-proxy-ui-design.md))
> and [`docs/config-schema.md`](../../config-schema.md). All v1 guardrails
> (no `ssl` in cache_params, routing-strategy enum, no literal secrets, master
> key server-side) carry forward unchanged.

## Goals

1. A **staged Save → global Apply** workflow (decouple editing from the ~25s restart).
2. **Dashboard** rebuilt to the prototype (KPI cards + health summary).
3. **Routing** exposes timeout / cooldown / allowed_fails / retry_after.
4. **Caching** screen becomes a clear **read-only** status panel.
5. **Provider Keys** vault — UI-owned encrypted credentials in an app DB table, **materialized into `config.yaml`** on apply (LiteLLM reloads config on restart, so they persist) — Models pick from it.
6. **Models** gain mode/endpoint, custom costs, and a pre-save **Test connection** + per-model **health**.
7. Two **LiteLLM catalog syncs** (pricing + provider-endpoints) feeding Models auto-fill.

## Non-goals

- No change to the config-only model (`store_model_in_db: false`; `config.yaml` authoritative for models/routing/caching). NOTE: `config.yaml` now also carries materialized provider-key secrets (see Provider Keys) — so it becomes a secret-bearing, gitignored file.
- No editing of cache backend connection from the UI (it's compose infrastructure).
- No `api_base` auto-population (the provider JSON has no URLs; only custom/self-hosted set it, manually).

## Verified LiteLLM facts (researched against `main`)

- **Pricing catalog:** `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json` (~1.5 MB, ~2,775 models). Per-model: `input_cost_per_token`, `output_cost_per_token`, `max_input_tokens`/`max_output_tokens`, `mode`, `litellm_provider`, `supports_*`. Skip the `sample_spec` key.
- **Provider endpoints:** `https://raw.githubusercontent.com/BerriAI/litellm/main/provider_endpoints_support.json` (~90 KB, 157 providers). Per provider: `display_name`, docs `url`, `endpoints` (boolean map: chat_completions, embeddings, rerank, …). **No `api_base`.**
- **Test connection:** `POST /health/test_connection` — body `{litellm_params, mode, model_info}`; tests a **candidate not yet saved**; returns `{status, result}`. Rejects request-supplied `os.environ/` refs. **Requires a connected DB** (we have Postgres → OK).
- **Per-model health:** `GET /health?model=<name>` makes a live call; with `general_settings.background_health_checks: true` + `health_check_interval` it returns **cached** results (`healthy_endpoints`/`unhealthy_endpoints`/counts). Use cached.
- **Credentials:** management API `POST/GET/DELETE/PATCH /credentials` (`litellm/proxy/credential_endpoints/endpoints.py`); object `{credential_name, credential_values, credential_info}`; model references via `litellm_params.litellm_credential_name`. **Writes require a DB** (we have it); reads come from in-memory `litellm.credential_list`, populated from the DB **and/or** a `config.yaml` `credential_list:`. **Open risk:** whether DB credentials reload across a proxy restart in config-only mode — see Risks (v2.2 spike).

---

## Cross-cutting: Save → Apply workflow

**Today:** each write does validate → write → restart → verify (a restart per setting).
**v2:** decouple.

- **Save** (per setting / per screen): validate the section + atomically write `config.yaml` (backup as today). **No restart.** Staged on disk.
- **Baseline:** `apply_config` keeps `config/.applied.yaml` = the last *successfully applied* config (the known-good rollback target). `config/.applied.yaml` is gitignored.
- **Pending = `config.yaml` content ≠ `.applied.yaml` content.** (Also flags hand-edits — intended.)
- **Apply** (global bar, top-left in the shell, shown only when pending; labelled e.g. *"3 unapplied changes — Apply"*): one restart → verify health + `/v1/models` → on success copy `config.yaml`→`.applied.yaml`; on failure restore `.applied.yaml`→`config.yaml` + restart back (rollback) and report. One restart applies all staged edits.

### Backend changes (`config_store` / `safe_apply` → split)
- `save_config(path, raw)` — validate (guardrails) + atomic write + backup. No reload. (Extracted from `write_config`.)
- `apply_config(path, reloader)` — snapshot baseline, restart, verify health + `/v1/models`, update or restore baseline. (Refactor of `safe_apply`, now baseline-driven.)
- `pending_status(path)` — compares `config.yaml` vs `.applied.yaml`; returns `{pending: bool, summary: [...changed sections...]}`. On first run (no `.applied.yaml`) treat current `config.yaml` as the baseline (write it) so a fresh deploy isn't "pending".

### API
- `PUT /api/config` → **save only** (no restart); returns `{ok, pending: true}`.
- `POST /api/apply` → run `apply_config`; 200 on applied, 409 on reload-failure-rolled-back.
- `GET /api/apply/status` → `{pending, summary}` for the Apply bar.
- (Granular `PUT /api/config/{section}` optional; the screens may PUT the whole merged config as today, since save is now cheap.)

### Frontend
- Shell renders a **global Apply bar** (top-left) bound to `GET /api/apply/status` (polled + refreshed after each save). Each config screen's **Save** persists its section (staged) and shows a per-setting "saved" state; **Apply** triggers the restart with the existing applying/rolled-back banners.

---

## Phase v2.1 — Apply model + Dashboard + Routing + Caching

### Dashboard (rebuild to prototype)
KPI cards: **Proxy** (healthy/db), **Models** (count from `/v1/models` or config), **Virtual keys** (count from `/api/keys`), **Spend (30d)** (from `/api/usage`), **Cache** (on/off). Plus a health summary line and the pending-apply banner. All from existing endpoints; no new backend.

### Routing (additions)
Add individually-saved fields to the existing screen: `timeout`, `cooldown_time`, `allowed_fails`, `retry_after` (alongside `routing_strategy`, `num_retries`, fallbacks). All under `router_settings`; validated by `config_store`.

### Caching (read-only status panel)
Replace the editable form with a **read-only** panel:
- Caching **on/off** (`litellm_settings.cache`), **type** (`redis`), **TTL/namespace** if set — read from `config.yaml`.
- **Effective backend**: `valkey : 6379`, resolved by passing `REDIS_HOST`/`REDIS_PORT` to the UI container for display, with the note: *"Cache backend is provisioned in `docker-compose.yml` (the `valkey` service, reached via Docker DNS). Change it there, not here."*
- No write path. (`config.yaml` `cache_params` are preserved untouched when other sections are saved.)

---

## Phase v2.2 — Provider Keys + Models v2

### Provider Keys (new screen, UI-owned vault → materialized into config.yaml)
- **Spike finding (done):** in config-only mode LiteLLM's own `POST /credentials` writes the DB row but does **NOT** reload it on restart — so a pure LiteLLM-DB vault vanishes on every Apply (= restart). Therefore **the UI owns the vault.**
- New `credentials_store` (app DB table `ui_credentials`: name, provider, **encrypted** value — Fernet, key derived from `SESSION_SECRET`) + `/api/credentials` routes (login-gated): create (type the key → encrypted at rest), list (masked), delete. The UI — not LiteLLM — is the source of truth.
- **Materialization:** on save/apply the backend renders the vault into `config.yaml`'s `credential_list` with **literal** values (decrypted); models reference them via `litellm_credential_name`. LiteLLM reloads `config.yaml` on restart → credentials persist and reload. Adding/removing a credential re-materializes → marks pending.
- **Consequence — `config.yaml` becomes secret-bearing:** written `0600` (not 0644); the live `config/config.yaml` is **gitignored** (commit `config/config.yaml.example`, a secret-free bootstrap; seed the live file from it on first run); the no-literal-secrets guardrail **exempts** the materialized `credential_list`; config **export redacts** credential values.

### Models v2 (add/edit form gains)
- **Credential**: a dropdown of saved provider keys (→ `litellm_credential_name`), with **+ New key** that opens the Provider-Keys create flow (DB-backed, the primary path). Advanced/escape hatch: reference an env var directly (`api_key: os.environ/<VAR>`) for the config-only/no-DB style — never a literal key (the guardrail rejects those).
- **Mode / endpoint**: select `chat`/`embedding`/`image_generation`/… (→ `model_info.mode`), options narrowed by the provider's supported endpoints (from the v2.3 catalog; static fallback list until synced).
- **Custom costs**: `input_cost_per_token` / `output_cost_per_token` (→ `litellm_params`/`model_info`), pre-filled from the catalog when available, overridable.
- **Test connection** (pre-save): a **Test** button on the form → `POST /api/models/test` → `POST /health/test_connection` with the form's current `litellm_params`+`mode`. Works **before Save**; shows success/error. (Uses inline key or a selected DB credential; not a raw `os.environ/` ref.)
- **Per-model health** (list): enable `general_settings.background_health_checks: true` (+ `health_check_interval`, default 300s); `GET /api/models/health` → cached `GET /health`; show green/red per model. New `GET /api/models/health` route.

---

## Phase v2.3 — LiteLLM catalog syncs

- New `catalog` module + Postgres tables `model_pricing` (model → cost/context/mode/supports_*/provider) and `provider_endpoints` (provider → endpoint matrix + docs url + display_name).
- An APScheduler job (reuse the housekeeping scheduler pattern) fetches the two GitHub JSONs on a **configurable schedule** (`CATALOG_SYNC_ENABLED`, `CATALOG_SYNC_INTERVAL_DAYS` default 7) + a manual **"Sync now"** + last-synced timestamp (surfaced in Settings or a small Catalog panel).
- `GET /api/catalog/model/{name}` (cost/context/mode) and `GET /api/catalog/providers` (endpoint matrix) feed the Models form's auto-fill + endpoint narrowing. Sync writes are idempotent upserts; fetch failures keep the last-good data (logged, surfaced as a stale-since timestamp).
- `api_base` is **not** sourced here (no URLs in the JSON); custom/self-hosted set it manually.

---

## Data flow (v2 highlights)

1. **Edit:** screen → `PUT /api/config` (save, staged) → Apply bar shows pending (`GET /api/apply/status`).
2. **Apply:** Apply bar → `POST /api/apply` → restart → verify → baseline updated (or rolled back).
3. **Add model:** form → (optional) `POST /api/models/test` → Save (staged) → Apply.
4. **Catalog:** scheduler/Sync now → fetch JSON → upsert DB → Models form reads `/api/catalog/*`.

## Error handling

- Save validation failure → 422, nothing written (guardrails before disk).
- Apply reload failure → 409, baseline restored + proxy restarted onto it.
- Credentials/test/health/catalog upstream errors → 502 with detail; catalog keeps last-good on fetch failure.
- `/api/apply/status` and `/api/models/health` degrade to safe defaults (not pending / unknown) on backend error.

## Security

- Master key + DB stay server-side. Provider keys are stored **encrypted at rest** in the UI's `ui_credentials` table (Fernet, key derived from `SESSION_SECRET`); `GET /api/credentials` returns them **masked** (never the plaintext).
- The vault is **materialized into `config.yaml`'s `credential_list` as literals** on apply — so **`config.yaml` is secret-bearing**: written `0600`, the live file is gitignored (committed secret-free `.example` instead), the no-literal-secrets guardrail **exempts** `credential_list`, and config **export redacts** credential values. Model entries themselves stay secret-free (they reference `litellm_credential_name`).
- Catalog fetch is over HTTPS from the pinned `raw.githubusercontent.com/BerriAI/litellm/main` URLs; parsed as data (no code execution).

## Testing

- Backend TDD: `save_config`/`apply_config`/`pending_status` (baseline + rollback), `credentials_client` + routes, `models/test` + `models/health` routes (mocked httpx), `catalog` fetch/parse/upsert + `/api/catalog/*` (mocked fetch + DB), routing/dashboard endpoints.
- Real-stack integration per phase (Playwright + API): staged-save→Apply→rollback; provider-key create→model-uses-it; test-connection pre-save; catalog sync→auto-fill.

## Risks / spikes

- **v2.2 credentials (spike DONE):** LiteLLM does NOT reload DB credentials on restart in config-only mode → the UI owns the vault (`ui_credentials`, encrypted) and materializes it into `config.yaml`'s `credential_list` (which LiteLLM *does* reload). Consequence accepted: `config.yaml` becomes secret-bearing (0600 / gitignored live / committed `.example` / export-redacted). This reverses the v2.1 `0644` + git-tracked + no-secrets stance for `config.yaml` (which only held while config was secret-free).
- **Background health cost** — live provider calls; mitigate with `background_health_checks` cache + a sane interval; document it's opt-in-ish.
- **Catalog size** — 1.5 MB pricing JSON; store parsed rows in Postgres, not the blob; refresh weekly.
- **`.applied.yaml` baseline** must be gitignored + handled on first run (seed from current config).

## Phasing

v2.1 (apply model + dashboard + routing + caching) → v2.2 (provider keys + models v2) → v2.3 (catalog syncs). Each is a separate implementation plan, shippable on its own.
