# LLM-Proxy Admin UI

A purpose-built, Apple-HIG admin UI for this LiteLLM proxy stack — a reliable
replacement for the bundled LiteLLM UI.

> **Status: shipped** (Phases 1–5). Foundation/auth, models + routing with
> safe-apply, virtual keys + budgets, usage & spend, and caching + housekeeping +
> export/import + dark mode are all live and released to GHCR. Design:
> the [spec](superpowers/specs/2026-06-07-llm-proxy-ui-design.md),
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
  caching (`store_model_in_db: false`). The UI is a validating editor for that
  file; changes are applied via a controlled **container restart** (~25s; SIGHUP
  is a no-op on this LiteLLM image) through a scoped `docker-socket-proxy`, with
  health-verify and auto-rollback.
- **Virtual keys, budgets, and spend** are read/written through the LiteLLM
  management API. The master key stays **server-side only** (never in the
  browser).
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
[`docker-compose.yml`](../docker-compose.yml), pulled from GHCR
(`ghcr.io/tekgnosis-net/llm-proxy-ui:latest`) — no local build needed.

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

- **Dashboard** — proxy health (reachable, DB connected).
- **Usage & Spend** — total spend, by model, by key, daily activity (last 30d).
- **Models** — provider-driven CRUD (OpenAI/Anthropic/Azure/Bedrock/Gemini/local); secrets emitted as `os.environ/<VAR>`.
- **Routing** — strategy (valid enum only), retries, fallbacks.
- **Caching** — Redis/Valkey cache config (never an `ssl` key).
- **config.yaml** — read-only view of the effective config.
- **Virtual Keys** — create (one-time plaintext shown once) / list / delete with budgets, model allowlist, expiry.
- **Housekeeping** — DB stats + maintenance.
- **Settings** — export/import config + dark mode.

Config-editing screens use the **safe-apply pipeline**: validate (schema +
guardrails) → atomic write + backup → restart proxy → verify health & `/v1/models`
→ auto-rollback on failure.

## A note on `config.yaml` ownership

The UI writes `config.yaml` from inside its container (running as root), so after
the first UI save the file is `root`-owned, mode `0644` — host-readable (it holds
no secrets, only `os.environ/` refs) but not host-writable. To hand-edit, use the
UI, or `sudo` (or `rm` + restore from git, since the host owns the `config/` dir).

## CI/CD

`main` runs **semantic-release** (conventional commits → versions + GitHub
releases) and publishes the UI image to
`ghcr.io/tekgnosis-net/llm-proxy-ui:<version>` + `:latest`.

## Credit

Built on **[LiteLLM](https://github.com/BerriAI/litellm)** (BerriAI) — the proxy
that powers all routing, caching, key management, and spend tracking. This UI is
a front-end + deployment around it. See the repo [README](../README.md) for full
acknowledgements.
