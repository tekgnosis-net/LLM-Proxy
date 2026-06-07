# LLM-Proxy Admin UI

A purpose-built, Apple-HIG admin UI for this LiteLLM proxy stack — a reliable
replacement for the bundled LiteLLM UI.

> **Status: shipped** (Phases 1–5 + v2). Foundation/auth, models + routing,
> virtual keys + budgets, usage & spend, and caching + housekeeping +
> export/import + dark mode, plus the **v2** refinements: a staged **Save → Apply**
> workflow, a **Dashboard** with KPI cards, a **Provider Keys** vault, **Models v2**
> (test-connection / health / costs / credentials), and a **LiteLLM catalog sync**.
> All live and released to GHCR. Design: the v1
> [spec](superpowers/specs/2026-06-07-llm-proxy-ui-design.md) +
> [v2 spec](superpowers/specs/2026-06-07-llm-proxy-ui-v2-design.md),
> [clickable prototype](superpowers/specs/2026-06-07-llm-proxy-ui-prototype.html),
> the [config schema](../config-schema.md), and the per-phase
> [plans](superpowers/plans/). Screenshots: [`../README.md`](../README.md).

## Why it exists

The bundled LiteLLM UI was unreliable on this stack:

- Configuring the Redis cache via the UI injected an `ssl` key → LiteLLM bug
  **#10949** (SSLConnection even when `ssl: false`) → TLS handshake against
  plain Valkey hung → endless "Timeout connecting to server".
- The **Router Settings page has no save endpoint** — routing-strategy changes
  never persisted.
- With `store_model_in_db: true`, the **DB silently overrode `config.yaml`**,
  making the effective config opaque.

This UI fixes all three by design (guardrails + a single source of truth).

## How it works

- **One container** (`llm-proxy-ui`): a FastAPI backend serving a Svelte SPA.
- **`config.yaml` is the single source of truth** for models / routing /
  caching (`store_model_in_db: false`). The UI is a validating editor for that file.
- **Staged Save → global Apply (v2).** Each screen's **Save** validates and writes
  its section to `config.yaml` *without restarting* — the change is staged. A global
  **Apply** bar (top-left) appears whenever the on-disk config differs from the
  last-applied baseline (`config/.applied.yaml`); clicking it restarts the proxy
  **once** (~25s; SIGHUP is a no-op on this image) through a scoped
  `docker-socket-proxy`, verifies health + `/v1/models`, and on failure **rolls back**
  to the baseline and restarts onto it. So many edits apply in a single restart, and
  a bad config never sticks.
- **Provider Keys (v2):** a **UI-owned, encrypted credential vault** (an app DB
  table; keys typed in the UI, encrypted at rest via Fernet). On apply the vault is
  **materialized into `config.yaml`'s `credential_list`** (LiteLLM reloads config on
  restart, so credentials persist — LiteLLM's own DB credentials do *not* reload in
  config-only mode). Models reference a credential by name. Because of this,
  `config.yaml` now holds secrets — see "ownership" below.
- **Models v2:** add/edit gains a credential dropdown, mode/endpoint, custom
  input/output costs, a pre-save **Test connection** (`/health/test_connection`),
  and a per-model **health** dot (cached `background_health_checks`).
- **LiteLLM catalog sync (v2):** a scheduled (default weekly + on boot) +
  on-demand sync of LiteLLM's `model_prices_and_context_window.json` and
  `provider_endpoints_support.json` into Postgres, used to **auto-fill** model
  cost/context/mode in the Models form.
- **Virtual keys, budgets, and spend** are read/written through the LiteLLM
  management API. The master key stays **server-side only** (never in the
  browser); credential values are returned **masked** (`***`), never in plaintext.
- **DB housekeeping:** an opt-in UI-managed maintenance cron (APScheduler) that
  trims spend logs past a retention window and deletes expired keys, plus DB
  stats (size, row counts) and a manual "Run now". Cron is off unless
  `HOUSEKEEPING_ENABLED=true`; retention defaults to 90 days; the DELETE is
  parameterized + bounded.
- **Export / Import:** download `config.yaml`, or import one (validated + applied
  through the same safe-apply pipeline).
- **Auth:** a single admin password (`ADMIN_PASSWORD_HASH`, argon2) + signed
  session cookie. The argon2 hash's `$` must be escaped as `$$` in `.env` (docker
  compose interpolates `$` — see "Running it"), or login fails silently.

### Guardrails (the old bugs can't recur)

- The UI **never writes an `ssl` key** into `cache_params` (no TLS control on
  the Caching screen) → #10949 is impossible.
- Routing strategy is constrained to the valid enum (`cost-based-routing`, …);
  the bogus `lowest-cost` value is rejected.

## Running it

The UI runs as the `llm-proxy-ui` service in the root
[`docker-compose.yml`](../docker-compose.yml), pulled from GHCR (pinned to an
immutable release tag, e.g. `ghcr.io/tekgnosis-net/llm-proxy-ui:1.12.0`) — no
local build needed. To update, bump the tag (or use `:latest`).

```bash
./setup_env_helper.sh    # interactive: fills .env (keys, admin hash $$-escaped, …)
docker compose up -d     # pulls the images and starts the stack
# open http://<host>:8081  and log in
```

The `setup_env_helper.sh` helper generates the argon2 admin hash and escapes its
`$` as `$$` (docker compose interpolates `$` in `.env`, so an un-escaped hash
mangles to blank → silent login failure). To set it by hand instead, escape the
hash yourself:

```bash
docker compose run --rm --no-deps llm-proxy-ui \
  python -c "from app.auth import hash_password; print(hash_password('YOUR_PASSWORD'))" \
  | sed 's/[$]/$$/g'      # paste the $$-escaped result into ADMIN_PASSWORD_HASH
```

For local UI development, change the `llm-proxy-ui` service from `image:` to
`build: ./ui` and run `docker compose up -d --build`.

## Screens (all live)

- **Dashboard** — KPI cards: proxy health (reachable, DB), model count, virtual-key
  count, 30-day spend, cache on/off.
- **Usage & Spend** — total spend, by model, by key, daily activity (last 30d).
- **Models** — provider-driven CRUD (OpenAI/Anthropic/Azure/Bedrock/Gemini/local)
  with credential dropdown, mode/endpoint, custom input/output costs (catalog
  auto-fill), pre-save **Test connection**, and a per-model **health** dot.
- **Provider Keys** — UI-owned encrypted credential vault (add/list-masked/delete);
  materialized into `config.yaml` on Apply.
- **Routing** — strategy (valid enum only), retries, **timeout / cooldown /
  allowed_fails / retry_after**, fallbacks.
- **Caching** — **read-only** status panel (effective `valkey:6379`); the cache
  backend is provisioned in `docker-compose.yml`, not edited here.
- **config.yaml** — read-only view of the effective config (credential values
  redacted).
- **Virtual Keys** — create (one-time plaintext shown once) / list / delete with budgets, model allowlist, expiry.
- **Housekeeping** — DB stats + maintenance.
- **Settings** — export/import config (credentials redacted), **LiteLLM catalog
  sync** (last-synced + "Sync now"), and dark mode.

Each config screen's **Save** validates (schema + guardrails) and atomically writes
its section + a timestamped backup — staging the change. The global **Apply** then
restarts the proxy once, verifies health & `/v1/models`, and **rolls back to the
last-applied baseline** on failure.

## A note on `config.yaml` (now secret-bearing)

Since v2 the UI materializes provider keys into `config.yaml`'s `credential_list`
as **literal values** (so they survive the restart-based Apply — LiteLLM reloads
config on restart but does *not* reload its DB credential vault in config-only
mode). Consequences:

- The live **`config/config.yaml` is `git`-ignored** and written **mode `0600`**,
  `root`-owned (it holds secrets). The repo commits **`config/config.yaml.example`**
  — a secret-free bootstrap (only `os.environ/` refs) that the app copies to
  `config.yaml` on first run.
- Timestamped backups (`config.yaml.bak.*`) and the apply baseline
  (`config/.applied.yaml`) are likewise `0600` and git-ignored.
- The UI never returns credential plaintext to the browser: `GET /api/config` and
  config **export** redact `credential_list` values to `***`.
- To hand-edit, use the UI, or `sudo` (the file is root-owned `0600`). Keep
  `LITELLM_SALT_KEY`/`SESSION_SECRET` stable — the vault's encryption key derives
  from `SESSION_SECRET`, so rotating it makes stored keys undecryptable.

## CI/CD

`main` runs **semantic-release** (conventional commits → versions + GitHub
releases) and publishes the UI image to
`ghcr.io/tekgnosis-net/llm-proxy-ui:<version>` + `:latest`.

## Credit

Built on **[LiteLLM](https://github.com/BerriAI/litellm)** (BerriAI) — the proxy
that powers all routing, caching, key management, and spend tracking. This UI is
a front-end + deployment around it. See the repo [README](../README.md) for full
acknowledgements.
