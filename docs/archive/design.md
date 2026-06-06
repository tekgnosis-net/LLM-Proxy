# LLM-Proxy — Design Notes

Architecture reference for this private LiteLLM deployment. The
[`README.md`](../README.md) covers day-to-day operation; this doc captures the
*why* behind the choices so future-me (or anyone forking this) doesn't have to
reverse-engineer them.

## Goals

1. **UI-driven configuration.** All model definitions, virtual API keys,
   teams, users, and budgets are added through the LiteLLM admin UI rather
   than by hand-editing `config.yaml`. This makes day-to-day operation a
   browser activity, not a YAML activity.
2. **Bind mounts everywhere.** Every persistent file — proxy config, Postgres
   data, Valkey data, secrets — lives on the host filesystem under `./config/`
   or `./data/`. Editable, inspectable, backup-friendly with `tar`/`rsync`.
3. **Self-contained.** One `docker compose up -d` brings the entire stack
   online. No external Postgres, no separate Redis instance to manage.

## Architectural decisions

### Why Postgres is bundled

The LiteLLM UI is essentially a CRUD front-end for a database. Without
`DATABASE_URL` set, the UI loads but the "Add Model", "Virtual Keys",
"Teams", and "Users" pages all become read-only or error out. Bundling
`postgres:16-alpine` in the same compose file makes the stack self-contained
and means goal #1 (UI-driven config) is reachable on a fresh machine with no
external dependencies.

The `data/postgres` bind mount keeps the DB durable across `docker compose
down` / `up` cycles and across container image upgrades.

### The `STORE_MODEL_IN_DB=true` flag

The single most important environment variable in this setup. Without it, the
UI's "Models" page is read-only — models can only be added by editing
`config.yaml` and restarting. With it, the UI writes new model definitions to
the Postgres `LiteLLM_ModelTable` table, and `config.yaml` becomes a tiny
bootstrap file that you can leave alone.

This is why the `config.yaml` mount is `:ro` — the proxy never needs to write
to it once the DB is the source of truth.

### Why Valkey, not Redis

LiteLLM uses Redis for two things: response caching and distributed
rate-limit state. Both go through the standard Redis wire protocol (RESP).

Redis Inc. moved Redis from BSD-3 to the non-OSI-approved RSALv2/SSPLv1
license in March 2024. The Linux Foundation forked the last BSD-3 commit as
**Valkey**, kept BSD-3 licensing, and continues active development. Valkey:

- Speaks the same RESP protocol → LiteLLM doesn't know or care.
- Uses the same default port (6379) and config syntax.
- Performance benchmarks are at parity or slightly ahead.
- Image: `valkey/valkey` on Docker Hub.

So we get the functionality with a license that matches the rest of the
stack.

### Healthchecks and `depends_on: service_healthy`

Plain `depends_on: [postgres]` waits for the container to *start*, not for
Postgres to actually accept connections. On a cold `docker compose up -d`,
the LiteLLM proxy would race the Postgres init and crash-loop on first boot
roughly half the time.

`depends_on` with `condition: service_healthy` makes the proxy block on the
Postgres healthcheck (`pg_isready -U $POSTGRES_USER -d litellm`) and the
Valkey healthcheck (`valkey-cli ping`) passing first. Cold boot is now
deterministic.

The LiteLLM healthcheck has `start_period: 30s` to give the proxy room to run
its Prisma migrations against an empty Postgres on first boot without being
marked unhealthy during that window.

### Read-only `config.yaml` mount

Mounting `./config/config.yaml:/app/config.yaml:ro` does two things:

1. **Security boundary.** A compromised proxy process cannot rewrite its own
   boot config. The container's filesystem is also read-only-by-default for
   that path.
2. **Source-of-truth signal.** The `:ro` makes it explicit to anyone reading
   the compose file that authoritative state lives in Postgres, not in the
   YAML file.

If you ever do need to change `config.yaml` (e.g. to tweak caching, change
telemetry, or set a `drop_params` rule), edit the host file and run
`docker compose restart litellm` — the bind mount picks up the host change
on the next boot.

### Why no host port for Postgres or Valkey

Defense in depth. Neither needs to be reachable from the host or LAN. They
get reached by the LiteLLM container on the internal compose network
(`postgres:5432`, `valkey:6379`). Exposing them would only widen attack
surface for no operational benefit.

If you ever need to connect a SQL client for debugging, use:

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" litellm
```

### Why two separate secrets (`MASTER_KEY` vs `SALT_KEY`)

They have different rotation properties:

- **`LITELLM_MASTER_KEY`** — auth credential. Safe to rotate at any time:
  change in `.env`, `docker compose up -d`, the new value takes effect.
- **`LITELLM_SALT_KEY`** — symmetric key used to encrypt provider API keys
  (OpenAI, Anthropic, etc.) at rest in the Postgres `LiteLLM_ProviderKey`
  table. **Cannot be rotated** once provider keys have been added — every
  encrypted column would become undecryptable. Treat it as "set once, back
  it up forever, never change."

Keeping them as distinct env vars makes this distinction explicit in code.

## File layout (deployment view)

```
.
├── docker-compose.yml        # 3 services, healthchecks, bind mounts
├── .env                      # secrets (NOT in git — see .gitignore)
├── .env.example              # template, committed
├── .gitignore                # excludes .env and data/
├── README.md                 # operator quickstart + ops cookbook
├── docs/
│   └── design.md             # this file
├── config/
│   └── config.yaml           # minimal boot config, mounted :ro
└── data/                     # bind-mounted persistent state (NOT in git)
    ├── postgres/             # PG datadir, owned by UID 999
    └── valkey/               # Valkey AOF + RDB
```

## Service topology

```
                    ┌─────────────────────────────────────┐
host:4000 ─────────▶│       litellm                        │
                    │  ghcr.io/berriai/litellm:main-stable │
                    │       /ui  /chat/completions  etc.   │
                    └──────┬────────────────┬──────────────┘
                           │                │
                  postgres:5432       valkey:6379
                           │                │
                  ┌────────▼─────────┐ ┌────▼──────────────┐
                  │  postgres:16-    │ │  valkey/valkey:8- │
                  │  alpine          │ │  alpine           │
                  │  ./data/postgres │ │  ./data/valkey    │
                  └──────────────────┘ └───────────────────┘
```

Only `litellm` publishes a host port. The Postgres and Valkey services are
reachable only via Docker's internal `litellm_default` network.

## Backup strategy (recommended)

The two things that matter for disaster recovery:

1. **`LITELLM_SALT_KEY`** — without it, the encrypted provider keys in Postgres
   are useless. Back it up to a password manager or secrets vault.
2. **Postgres dump** — captures all UI-managed state (models, virtual keys,
   teams, users, spend logs).

   ```bash
   docker compose exec postgres pg_dump -U "$POSTGRES_USER" litellm \
       | gzip > backup-$(date +%F).sql.gz
   ```

   To restore on a fresh machine:

   ```bash
   gunzip -c backup-YYYY-MM-DD.sql.gz \
       | docker compose exec -T postgres psql -U "$POSTGRES_USER" litellm
   ```

The Valkey data is cache — losing it is fine, it rebuilds on demand.

## Trade-offs and known limitations

- **Single-node.** This compose stack is fine for personal use, a homelab, or
  a small team. For HA you'd want a managed Postgres, a Valkey cluster or
  Sentinel setup, and multiple LiteLLM replicas behind a load balancer.
- **TLS terminates at the proxy.** There's no reverse proxy in the stack —
  LiteLLM speaks plain HTTP on port 4000. If you expose this beyond
  localhost, put it behind Caddy / nginx / Traefik with a real cert.
- **No log rotation configured.** Docker's default JSON-file driver will grow
  unbounded; if you keep this running long-term, add `logging:` directives
  with `max-size` and `max-file` to the services.
