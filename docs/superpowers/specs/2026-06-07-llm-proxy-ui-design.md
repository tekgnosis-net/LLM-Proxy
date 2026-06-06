# LLM-Proxy Admin UI — Design Spec

- **Status:** Draft for review
- **Date:** 2026-06-07
- **Owner:** kumar
- **Visual reference:** clickable prototype committed alongside this spec at
  `docs/superpowers/specs/2026-06-07-llm-proxy-ui-prototype.html` (open in a browser)

## Context

The LiteLLM proxy is excellent as a gateway, but its **bundled admin UI is
unreliable on this stack** — we hit a string of bugs this week:

1. Configuring the Redis cache via the UI writes an `ssl` key, which triggers
   LiteLLM bug **#10949** (SSLConnection even when `ssl: false`) → TLS handshake
   against plain Valkey hangs → endless "Timeout connecting to server".
2. The **Router Settings page has no save endpoint** (`/router/settings` is
   GET-only) → routing-strategy changes never persist ("reverts to
   simple-shuffle").
3. With `store_model_in_db: true`, the **DB silently overrides `config.yaml`
   and env vars**, so the effective config is opaque and edits land in
   surprising places.
4. **Routing precedence is invisible** (per-key `router_settings` > team >
   global > config.yaml), so global settings appear ignored.

**Goal:** a purpose-built, Apple-HIG admin UI that manages this proxy
*reliably and reproducibly* — config as a single version-controlled file,
stateful resources via the proxy API, and guardrails that make the above bugs
impossible to reintroduce. Ships as a container in the existing compose stack.

## Goals

- Manage **models, routing, caching** through `config.yaml` as the single
  source of truth (`store_model_in_db: false`).
- Manage **virtual keys, budgets, spend/usage** through the LiteLLM management
  API.
- **Apple-HIG**, full-viewport responsive web app (iCloud.com feel).
- Ship as one container in the existing `docker-compose` stack.
- **GitOps**: `config.yaml` is the versioned source; UI offers export/import
  snapshots.
- **Guardrails**: the UI can never write an `ssl` cache key or an invalid
  routing strategy (the two bugs that bit us).

## Non-goals (v1)

- Full teams/users RBAC, multi-admin, SSO/OIDC.
- Guardrails config, MCP-servers config, live log streaming.
- Replacing the LiteLLM gateway — we keep it as-is.

## Architecture

```
                     Admin browser (LAN)
                          │ https + session cookie (admin password)
                          ▼
   ┌───────────────────────────────────────────────┐
   │  llm-proxy-ui   (NEW container)                 │
   │   Svelte SPA  ⇄ /api ⇄  FastAPI backend         │
   │     • read/write/validate config.yaml           │
   │     • LiteLLM API client (master key, srv-side) │
   │     • auth/session                              │
   └───┬──────────────┬───────────────────┬─────────┘
       │ bind mount RW │ http              │ http (scoped)
       ▼               ▼                   ▼
   config.yaml     litellm:4000      socket-proxy (NEW)
   (host file,     /key /spend        → SIGHUP litellm
    shared)        /health            (reload config.yaml)
       ▲                                   │
       └─ litellm mounts same file (RO) ◄──┘
                            │
                            ▼
              Postgres (keys/spend) · Valkey (cache)
```

**Decisions locked in brainstorming:**

| Decision | Choice |
|---|---|
| Source of truth | Config-only (`store_model_in_db: false`); `config.yaml` authoritative for models/routing/cache |
| Reload | Apply config via a controlled **container restart** (~25s) through a **scoped docker-socket-proxy** (UI has no raw socket). NOTE: SIGHUP is a no-op on `main-stable` — see the Phase 2 reload note below |
| Stack | FastAPI backend + **Svelte** SPA, single container (multi-stage build) |
| Auth | Admin password (`.env`, hashed) + session cookie; **master key server-side only** |
| Framing | Full-viewport Apple-HIG web app (no desktop-window chrome) |
| v1 scope | Models · Routing · Virtual keys/budgets · Spend dashboard · Prompt caching · **DB housekeeping** |

**Why config-only is safe:** the feared "config-only drops models" issue
(#25350) was investigated and is a **false alarm** — the reporter retracted it
as their own upstream gateway's fault. We are on the latest release (v1.87.1).

## Docker-compose changes

- **litellm**: set `STORE_MODEL_IN_DB: "false"`; keep `config.yaml` bind mount
  (it stays read-only *to litellm*; the host file is writable by the UI).
- **NEW `llm-proxy-ui`**: built from `./ui` (multi-stage: build Svelte → serve
  via FastAPI). Mounts the host `config/config.yaml` **read-write**. Env:
  `LITELLM_BASE_URL=http://litellm:4000`, `LITELLM_MASTER_KEY`,
  `ADMIN_PASSWORD_HASH`, `SOCKET_PROXY_URL`, `SESSION_SECRET`, and
  `DATABASE_URL` (Postgres — for DB housekeeping + stats only). Publishes
  `${UI_PORT:-8081}:8080`. Depends on litellm + postgres healthy.
- **NEW `socket-proxy`** (`tecnativa/docker-socket-proxy`): mounts the Docker
  socket and exposes only container POST actions (`POST=1`, `CONTAINERS=1`, all
  other API families `0`) on an **internal network reachable only by the UI**.
  Caveat: this proxy scopes by API *family*, not per-container — for true
  single-action / single-container scoping, run a ~20-line custom reloader
  (does only `kill -HUP litellm`) behind it. See Risks. This keeps the *UI*
  itself free of any Docker access either way.
- **.env.example**: add `UI_PORT`, `ADMIN_PASSWORD_HASH`, `SESSION_SECRET`.

## Backend (FastAPI) — components

Each module has one job, a clear interface, and is unit-testable in isolation:

- **`auth`** — login (verify password hash), session cookie issue/verify,
  `login_required` dependency.
- **`config_store`** — load/parse/validate/write `config.yaml`. Pydantic models
  mirror LiteLLM's schema per [`docs/config-schema.md`](config-schema.md)
  (`model_list`/`litellm_params`/`model_info`, `router_settings`,
  `litellm_settings.cache_params`, `general_settings`) — preserving unknown keys
  on round-trip. Produces a diff vs the on-disk file before applying.
  **Guardrails live here** (see below).
- **`reloader`** — after a successful write, call the socket-proxy to `SIGHUP`
  litellm, then poll `/health/readiness` until the proxy is back; returns
  success/failure to the UI.
- **`litellm_client`** — thin async client for the management API (keys, spend,
  health) using the server-side master key.
- **`housekeeping`** — APScheduler jobs + manual triggers for DB maintenance
  (prune expired keys, trim error/audit logs beyond a window, `VACUUM
  (ANALYZE)`); also serves read-only DB stats. Uses a direct Postgres
  connection (`DATABASE_URL`). Jobs are idempotent, batched, and logged.
- **`routes`** — `/api/auth/*`, `/api/config/{models,routing,cache}`,
  `/api/keys/*`, `/api/usage/*`, `/api/housekeeping/*`, `/api/db/stats`,
  `/api/health`, `/api/config/export|import`.
- **static** — serves the built Svelte SPA.

### Guardrails (make the known bugs impossible)

- **Cache:** `config_store` never serializes an `ssl` (or `ssl_check_hostname`)
  key into `cache_params`. The Caching screen has no TLS control. (Prevents
  #10949.)
- **Routing strategy:** must be one of the valid enum
  (`simple-shuffle`, `least-busy`, `usage-based-routing`,
  `usage-based-routing-v2`, `latency-based-routing`, `cost-based-routing`).
  `lowest-cost` is rejected (it's only a docs nickname).
- **Write-then-verify:** every config write is validated (schema + `litellm`
  dry-parse if feasible) *before* replacing the file; the previous file is
  backed up so a bad apply can roll back.

## Screens (see prototype for visuals)

| Screen | Data path | Notes |
|---|---|---|
| **Dashboard** | API (health/spend) | Health pills, month stats, 7-day spend chart, over-budget alerts |
| **Models** | config.yaml | Deployments grouped by public model name; add/edit/remove → write+reload |
| **Routing** | config.yaml | Global strategy + per-group strategy + fallbacks; "Save & apply" = write+validate+SIGHUP+confirm |
| **Caching** | config.yaml | Enable, backend (Redis/Valkey or in-memory), host/port, TTL; **no TLS toggle**; live stats via `/cache/ping` |
| **Virtual Keys** | API | List w/ live budget bars; Create-Key sheet (alias, access, budget, rate limits, expiry, per-key routing override); revoke |
| **Usage & Spend** | API | 24h/7d/30d range; totals incl. cache savings; spend by day/model/key |
| **Settings** | mixed | Proxy connection + master-key status, reload-mechanism status, config.yaml viewer + **Export/Import snapshot (GitOps)**, admin password, theme (System/light/dark), version |
| **Housekeeping** | config + DB | DB stats cards; spend-log retention (config-backed); UI-cron schedule + targets (expired keys, logs, VACUUM); "Run cleanup now" w/ dry-run preview; last/next run |

## DB Housekeeping

Postgres grows over time — chiefly `LiteLLM_SpendLogs` (one row per request),
plus error/audit logs and expired keys. v1 manages this **two ways**:

1. **LiteLLM built-in retention (config path).** The UI sets, in `config.yaml`
   `general_settings`: `maximum_spend_logs_retention_period` (e.g. `30d`),
   `maximum_spend_logs_retention_interval` (purge cadence), and a
   `disable_spend_logs` toggle. The proxy enforces these on its own schedule;
   written/applied like any config change (validate → SIGHUP).
2. **UI-managed maintenance cron (APScheduler in the backend).** For what
   LiteLLM doesn't auto-clean: delete expired/over-age virtual keys, trim
   `LiteLLM_ErrorLogs`/audit logs beyond a window, and `VACUUM (ANALYZE)` to
   reclaim disk. Schedule (cron/interval) and per-target retention are set in
   the UI, with a manual **"Run cleanup now"** (dry-run preview shows row counts
   first) and last-run / next-run status.

This introduces a **third data path**: the UI backend connects to Postgres
directly (`DATABASE_URL`) for maintenance + read-only stats (table sizes, row
counts, oldest spend log, total DB size). Config stays on the file path; key/
spend management stays on the API path.

**Safety:** the cron only prunes append-only logs and expired keys (never alters
schema or live config tables); deletes are batched, idempotent, and logged. Use
a least-privilege Postgres role for the UI if feasible (see Security).

## Design system

- Full-viewport responsive layout; left sidebar (grouped: Overview /
  Configuration / Access & Spend / System); content max-width ~960px.
- System font stack (`-apple-system`, SF…); accent `#0a84ff`; **inset grouped
  cards** (macOS Settings look); hairline separators; soft shadows; 12px radii;
  translucent blurred sticky toolbar; Apple "pop-up button" controls.
- Light + dark via system preference (dark mode is polish, can land last).
- Svelte + CSS variables; no heavy component library.

## Config generation & safe-apply (THE critical safety path)

Generating `config.yaml` correctly is the highest-risk part of this app. A
malformed config **crashes the LiteLLM container on reload**; a *semantically*
wrong one (e.g. a model that doesn't load) is accepted but **fails silently**
(requests 404). The UI must make both impossible in normal use. Phase 2
implements a layered pipeline — nothing reaches the running proxy until it has
passed validation, and a bad apply self-heals.

> **Reload mechanism (Phase 2 spike finding):** `SIGHUP` does **not** reload
> config on `ghcr.io/berriai/litellm:main-stable` (it's a no-op). Applying a
> config change is therefore a **container restart** (~25s, brief proxy
> downtime) triggered via the socket-proxy (`POST /containers/<c>/restart`).
> Everywhere this section says "SIGHUP", read it as "trigger reload (restart)".
> The validate → backup → atomic write → restart → verify (health + `/v1/models`)
> → auto-rollback flow is unchanged; only the trigger differs.

1. **Typed generation, never free-form.** The UI builds config from structured
   form input into typed models (the `config_store` pydantic tree, **expanded in
   Phase 2 to mirror LiteLLM's expected schema** per the authoritative
   [`docs/config-schema.md`](../../config-schema.md) — every section's exact
   params, types, provider-specific `litellm_params`, secrets, and the forbidden
   keys), then serializes to YAML. Secrets are emitted as `os.environ/<VAR>`
   (never literals); unknown keys are preserved on round-trip. A raw-YAML editor,
   if offered, routes through the same validation — it can't bypass it.

2. **YAML structure validation (pre-write).** Parse the candidate YAML and
   validate its STRUCTURE against [`docs/config-schema.md`](../../config-schema.md):
   correct nesting (`litellm_params` only under `model_list[]` entries),
   required-field checks (every `model_list` entry has `model_name` +
   `litellm_params.model`; `cache_params.type` valid when `cache: true`), and the
   crash-class guardrails — **hard-reject** the #10949 ssl keys and any
   `routing_strategy` outside the valid enum (these crash the proxy on load).
   Nothing reaches disk until this passes.

3. **LiteLLM-fidelity validation (pre-apply) — prevents the crash.** Because a
   bad config can crash the proxy on SIGHUP, validate the candidate against
   LiteLLM's *actual* expectations BEFORE writing/reloading, without touching the
   running proxy. Phase 2 decides between: (a) importing LiteLLM's own config
   models in the UI backend (`pip install litellm`) and validating with them
   (highest fidelity, heavier image); (b) a throwaway dry-run (`litellm --config
   <candidate>` parse in a one-off container — authoritative, operationally
   heavier); (c) a faithful hand-maintained schema in `config_store` (lightest,
   must track LiteLLM releases). **Recommended:** (a) if image weight is
   acceptable, else (c) plus the runtime net below.

4. **Atomic write + backup.** Write a temp file, `os.replace()` it over
   `config.yaml` (atomic — the proxy never reads a half-written file), keeping a
   timestamped backup of the prior version.

5. **Apply + verify (catches crash AND silent-fail).** SIGHUP; then poll
   `/health/readiness` until healthy (bounded timeout) AND fetch `/v1/models`,
   confirming the expected `model_name`s are present. The model-presence check is
   what catches "accepted but silently wrong."

6. **Auto-rollback.** If the proxy doesn't return healthy, or `/v1/models`
   doesn't match, within the timeout → restore the backup → SIGHUP again →
   surface the error to the user. The proxy is never left broken; the user sees
   exactly what was rejected.

This pipeline is the spine of Phase 2; its plan TDDs each layer.

## Data flows

1. **Config edit** (Models/Routing/Caching): UI form → `PATCH /api/config/*` →
   backend validates (schema + guardrails) → writes `config.yaml` (backup
   prev) → reloader SIGHUPs litellm → polls `/health` → UI shows "applied"
   toast (or validation/reload error, with the file change rolled back on
   reload failure).
2. **Key create/revoke**: UI → `/api/keys/*` → `litellm_client` → proxy API →
   Postgres. Generated `sk-…` shown once.
3. **Spend/usage**: UI → `/api/usage/*` → proxy spend API → render charts.

## Error handling

- **Validation error** → shown inline in the form; nothing written.
- **Reload failure** → surfaced clearly; offer retry; if the proxy doesn't come
  back healthy, restore the backed-up `config.yaml` and reload again.
- **API/auth errors** → toast + actionable message; 401 → re-login.
- **Socket-proxy unreachable** → Settings shows the reload mechanism as
  degraded; edits still save to file, reload deferred with a banner.

## Security

- Master key lives only in the UI backend env; never sent to the browser.
- Admin password stored as a hash (argon2id); session cookie `HttpOnly`,
  `SameSite=Lax`, `Secure` when behind TLS.
- `socket-proxy` is the only thing with Docker access, allow-listed to one
  action on one container; the UI reaches it over an internal network.
- The UI backend holds `DATABASE_URL` for housekeeping/stats. Prefer a
  **least-privilege Postgres role** (DELETE/VACUUM on log tables + SELECT for
  stats) over reusing the full owner credential, if practical.
- Intended for LAN/VPN; put behind a reverse proxy + TLS for any wider exposure.

## Testing

- **Backend unit:** `config_store` parse/validate/write incl. the **ssl-guard**
  and **routing-enum** rejections; diff/rollback; `reloader` (mock socket-proxy
  + health); `litellm_client` (mock proxy); `auth`.
- **Frontend:** component tests for the form controls; **Playwright e2e** for
  the two critical flows — *add a model → reload → appears in `/v1/models`* and
  *create a key → it works against the proxy*.
- **Integration:** `docker compose up` smoke test of the config-edit→reload
  round trip and a key-create round trip.

## CI/CD

GitHub Actions on `main`:

- **Semantic-release** (`semantic-release`, node) reads the conventional commits
  already in use (`feat:` / `fix:` / `docs:` / `chore:`) → computes the next
  version, updates `CHANGELOG.md`, and creates a git tag + GitHub release.
- **Image publish:** when a release is cut, build `./ui` and push
  `ghcr.io/tekgnosis-net/llm-proxy-ui:<version>` **and** `:latest` to GHCR
  (workflow `permissions: packages: write`, login via `GITHUB_TOKEN`).
- Compose keeps `build: ./ui` for local/host builds (the live host lacks GHCR
  pull creds — see deployment notes); switch the UI service to
  `image: ghcr.io/tekgnosis-net/llm-proxy-ui:<tag>` once a registry pull token
  is configured on the host.

## Phasing (implementation order)

1. **Scaffold** — container + compose wiring (UI + socket-proxy), auth,
   `config_store` read/validate/view, Dashboard + health.
2. **Models + Routing** editing on the **safe-apply pipeline** (typed generation
   → schema+guardrail validation → LiteLLM-fidelity validation → atomic write +
   backup → SIGHUP → health/`/v1/models` verify → auto-rollback). This is the
   highest-risk phase — its plan TDDs each layer before any UI is wired.
3. **Virtual Keys + budgets** (API) incl. Create-Key sheet + per-key routing.
4. **Usage & Spend** dashboard.
5. **Caching** config + **DB housekeeping** (retention config + maintenance
   cron + DB stats) + **Export/Import snapshot** + dark-mode polish.

## Risks / open questions

- Exact LiteLLM spend/usage API endpoints + shapes (verify during Phase 4).
- SIGHUP reload race with the bind-mounted file write (write atomically: temp
  file + rename, then signal).
- Precise `docker-socket-proxy` allow-list to permit only `kill?signal=SIGHUP`
  on the litellm container (may need a thin wrapper if the proxy's granularity
  is per-endpoint, not per-container).
- `tecnativa/docker-socket-proxy` exposes API families, not per-container
  scoping — may need a minimal custom reloader behind it, or accept
  container-family scoping on a private network.
- Housekeeping cron must track LiteLLM's schema (table names like
  `LiteLLM_SpendLogs`, `LiteLLM_ErrorLogs` can change across versions) — pin to
  known tables, guard with existence checks, and verify after LiteLLM upgrades.

## References

- Prototype (visual source of truth): `2026-06-07-llm-proxy-ui-prototype.html`.
- Brainstorm decisions: config-only · FastAPI+Svelte · admin-password auth ·
  scoped socket-proxy reload · full-bleed Apple-HIG.
- **Config dictionary (authoritative): [`docs/config-schema.md`](../../config-schema.md)** — the LiteLLM config.yaml params the UI generates/validates.
- Background: `docs/archive/cost-routing-guide.md`; memory notes on LiteLLM bugs
  (#10949 SSL cache, router-settings no-setter, store_model_in_db precedence).
