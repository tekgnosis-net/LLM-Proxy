# LLM-Proxy

Self-hosted LiteLLM gateway (Docker Compose) — **LiteLLM proxy + Postgres +
Valkey** — managed by a **purpose-built Apple-HIG admin UI** that's replacing
LiteLLM's unreliable bundled UI. All configuration lives in a single
version-controlled `config.yaml` (config-only mode); the UI is a validating
editor for it, plus the proxy's API for virtual keys, budgets, and spend.

> **Status:** the custom UI (`llm-proxy-ui`) is in active development — Phase 1
> (foundation) is being built. See **[`docs/admin-ui.md`](docs/admin-ui.md)**,
> the [design spec](docs/superpowers/specs/2026-06-07-llm-proxy-ui-design.md),
> the [clickable prototype](docs/superpowers/specs/2026-06-07-llm-proxy-ui-prototype.html),
> and the [Phase 1 plan](docs/superpowers/plans/2026-06-07-llm-proxy-ui-phase1-foundation.md).
> Until the UI ships, manage models/routing by editing `config/config.yaml` and
> hot-reloading the proxy.

## Stack

| Service | Image | Purpose |
|---|---|---|
| `litellm` | `ghcr.io/berriai/litellm:main-stable` | OpenAI-compatible gateway (config-only; bundled UI not used) |
| `postgres` | `postgres:16-alpine` | Virtual keys, budgets, spend logs |
| `valkey` | `valkey/valkey:8-alpine` | Response cache + rate-limit state (Redis-protocol, BSD-3 fork) |
| `llm-proxy-ui` _(building)_ | `ghcr.io/tekgnosis-net/llm-proxy-ui` | Apple-HIG admin UI (FastAPI + Svelte) |
| `socket-proxy` _(building)_ | `tecnativa/docker-socket-proxy` | Scoped Docker access so the UI can SIGHUP-reload the proxy |

**Configuration model:** `config.yaml` is the single source of truth for
models, routing, and caching (`store_model_in_db: false`). Keys/budgets/spend
are stateful and live in Postgres, managed via the proxy API. See
[`docs/admin-ui.md`](docs/admin-ui.md) for the architecture and why we moved off
the bundled UI.

## Bind-mounted layout

```
.
├── docker-compose.yml
├── .env                 ← secrets (NOT in git)
├── .env.example         ← template
├── config/
│   └── config.yaml      ← single source of truth (models, routing, cache)
├── ui/                  ← the custom admin UI (FastAPI + Svelte)
└── data/
    ├── postgres/        ← Postgres datadir (persistent)
    └── valkey/          ← Valkey AOF + RDB persistence
```

Everything above is editable on the host. After editing `config/config.yaml`,
reload the proxy: `docker compose kill -s SIGHUP litellm` (hot reload, no full
restart) — or, once the UI is running, edit it there and click **Save & apply**.

## Quickstart

```bash
$EDITOR .env            # secrets were generated with random keys
docker compose up -d
docker compose ps       # wait for (healthy)
```

Proxy health:

```bash
curl -fsS http://localhost:${LITELLM_PORT:-4000}/health/readiness
```

Admin UI (once built): `http://<host>:${UI_PORT:-8081}` — see
[`docs/admin-ui.md`](docs/admin-ui.md) for first-time setup (admin password
hash + session secret).

## Secrets in `.env`

- **`LITELLM_MASTER_KEY`** — gates the proxy's admin API. The UI backend holds
  it **server-side only**. Safe to rotate.
- **`LITELLM_SALT_KEY`** — encrypts provider API keys in Postgres. **Do not
  rotate** after adding provider keys — it makes existing encrypted keys
  undecryptable. Back it up.
- **`ADMIN_PASSWORD_HASH`**, **`SESSION_SECRET`** — admin UI login (argon2 hash)
  and cookie signing. See [`docs/admin-ui.md`](docs/admin-ui.md).

Generate a random key: `echo "sk-$(openssl rand -hex 32)"`. `.env` is
`.gitignore`d — share `.env.example` only.

## Common operations

```bash
docker compose logs -f litellm                 # tail proxy logs
docker compose kill -s SIGHUP litellm          # hot-reload config.yaml
docker compose down                            # stop (data persists in ./data)
docker compose exec postgres pg_dump -U "$POSTGRES_USER" litellm > backup-$(date +%F).sql
```

If Postgres shows permission errors on first boot:
`sudo chown -R 999:999 data/postgres data/valkey` then `docker compose up -d`.

## Documentation

- **[`docs/admin-ui.md`](docs/admin-ui.md)** — the new admin UI (architecture, run, status).
- **[`docs/superpowers/specs/`](docs/superpowers/specs/)** — design spec + clickable prototype.
- **[`docs/superpowers/plans/`](docs/superpowers/plans/)** — implementation plans (per phase).
- **[`docs/archive/`](docs/archive/)** — legacy guides for the bundled LiteLLM UI (concepts still valid).

## CI/CD

`main` runs **semantic-release** (conventional commits → versioned GitHub
releases) and publishes the UI image to GHCR
(`ghcr.io/tekgnosis-net/llm-proxy-ui:<version>` + `:latest`).
