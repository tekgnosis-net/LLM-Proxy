# LLM-Proxy Admin UI

A purpose-built, Apple-HIG admin UI for this LiteLLM proxy stack — a reliable
replacement for the bundled LiteLLM UI.

> **Status: in active development.** Phase 1 (foundation) is being built now.
> This doc describes the target app; sections marked _(planned)_ land in later
> phases. Authoritative design: the
> [spec](superpowers/specs/2026-06-07-llm-proxy-ui-design.md) and
> [clickable prototype](superpowers/specs/2026-06-07-llm-proxy-ui-prototype.html)
> (open in a browser). Build steps: the
> [Phase 1 plan](superpowers/plans/2026-06-07-llm-proxy-ui-phase1-foundation.md).

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
  file; changes are applied with a hot **SIGHUP** reload (no full restart) via a
  scoped `docker-socket-proxy`.
- **Virtual keys, budgets, and spend** are read/written through the LiteLLM
  management API. The master key stays **server-side only** (never in the
  browser).
- **DB housekeeping** _(planned)_: LiteLLM's built-in spend-log retention plus a
  UI-managed maintenance cron (expired keys, log trimming, `VACUUM`) with DB
  stats.
- **Auth:** a single admin password (`ADMIN_PASSWORD_HASH`, argon2) + signed
  session cookie.

### Guardrails (the old bugs can't recur)

- The UI **never writes an `ssl` key** into `cache_params` (no TLS control on
  the Caching screen) → #10949 is impossible.
- Routing strategy is constrained to the valid enum (`cost-based-routing`, …);
  the bogus `lowest-cost` value is rejected.

## Running it

The UI ships as a service in the root [`docker-compose.yml`](../docker-compose.yml):

```bash
# generate an admin password hash + session secret (one-time)
SESSION_SECRET=$(openssl rand -hex 32)
docker compose build llm-proxy-ui
HASH=$(docker compose run --rm --no-deps llm-proxy-ui \
  python -c "from app.auth import hash_password; print(hash_password('YOUR_PASSWORD'))")
printf "UI_PORT=8081\nADMIN_PASSWORD_HASH=%s\nSESSION_SECRET=%s\n" "$HASH" "$SESSION_SECRET" >> .env

docker compose up -d
# open http://<host>:8081  and log in
```

## Screens

Dashboard · Models · Routing · Caching · Virtual Keys · Usage & Spend ·
Settings · Housekeeping — see the prototype for the visual design. Each is
filled in across the implementation phases.

## CI/CD

`main` runs **semantic-release** (conventional commits → versions + GitHub
releases) and publishes the UI image to
`ghcr.io/tekgnosis-net/llm-proxy-ui:<version>` + `:latest`.
