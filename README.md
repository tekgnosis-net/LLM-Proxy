# LiteLLM (Docker Compose, UI-driven)

Self-contained LiteLLM proxy stack — proxy + UI + Postgres + Valkey — wired so
that essentially all configuration (models, virtual keys, teams, users,
budgets) is done through the admin UI. All persistent files live on the host
as bind mounts, so you can still edit, back up, or inspect anything directly.

## Stack

| Service    | Image                                  | Purpose                                                                  |
|------------|----------------------------------------|--------------------------------------------------------------------------|
| `litellm`  | `ghcr.io/berriai/litellm:main-stable`  | OpenAI-compatible proxy + admin UI at `/ui`                              |
| `postgres` | `postgres:16-alpine`                   | Stores models, virtual keys, teams, users, spend logs                    |
| `valkey`   | `valkey/valkey:8-alpine`               | Response cache + rate-limit state (Redis-protocol, BSD-3 licensed fork)  |

Only `litellm` exposes a host port (4000). Postgres and Valkey are reachable
only on the internal compose network.

## Bind-mounted layout

```
.
├── docker-compose.yml
├── .env                 ← secrets (NOT in git)
├── .env.example         ← template
├── config/
│   └── config.yaml      ← minimal proxy boot config; UI takes over from here
└── data/
    ├── postgres/        ← Postgres datadir (DB state — persistent)
    └── valkey/          ← Valkey AOF + RDB persistence
```

Every file above can be edited on the host. After editing `config/config.yaml`,
run `docker compose restart litellm` to pick up the change. The contents of
`data/` should not normally be hand-edited, but they are normal files on disk
and can be backed up with `tar`/`rsync` while the stack is stopped.

## Quickstart

```bash
# 1. Inspect / customize secrets — a .env was generated with random keys.
$EDITOR .env

# 2. Bring the stack up.
docker compose up -d

# 3. Watch the proxy come online (first boot runs Prisma migrations).
docker compose logs -f litellm
```

When all three services report `(healthy)`:

```bash
docker compose ps
```

Open <http://localhost:4000/ui> and log in:

- **Username:** `admin`
- **Password:** the value of `LITELLM_MASTER_KEY` in `.env`

From the UI: add models under **Models → Add Model**, create virtual API keys
under **Virtual Keys**, set up teams/users/budgets, etc. Everything you do here
is persisted in Postgres.

## Sanity checks

```bash
# Proxy is alive
curl -fsS http://localhost:4000/health/liveliness

# Proxy can reach DB + cache
curl -fsS http://localhost:4000/health/readiness

# Valkey is being used as cache (after issuing a couple of /chat/completions calls)
docker compose exec valkey valkey-cli KEYS '*'
```

## About the secrets in `.env`

Two keys, both 256-bit random values prefixed `sk-`:

- **`LITELLM_MASTER_KEY`** — gates all admin endpoints and is the UI admin
  password. Safe to rotate: change in `.env`, then `docker compose up -d`.
- **`LITELLM_SALT_KEY`** — symmetric key used to encrypt provider API keys
  (OpenAI, Anthropic, etc.) before storing them in Postgres. **Do not rotate**
  after you've added provider keys through the UI — rotating makes existing
  encrypted keys undecryptable, forcing you to re-enter every one. Back this
  value up alongside your Postgres backups.

Generate fresh values any time with:

```bash
echo "sk-$(openssl rand -hex 32)"
```

`.env` is `.gitignore`d. If you re-share this repo, share `.env.example` only.

## Permission note for bind mounts

The Postgres entrypoint chowns `data/postgres` to its own UID (999) on first
boot, so things normally just work. If you see permission errors in
`docker compose logs postgres` on first start, run:

```bash
sudo chown -R 999:999 data/postgres data/valkey
```

…and restart the stack.

## Common operations

```bash
# Tail proxy logs
docker compose logs -f litellm

# Reload after editing config/config.yaml
docker compose restart litellm

# Stop the stack (data persists in ./data/)
docker compose down

# Stop and wipe state (deletes models, keys, teams, cache)
docker compose down && sudo rm -rf data/postgres data/valkey

# Upgrade LiteLLM to the newest stable
docker compose pull litellm && docker compose up -d litellm

# Backup Postgres while the stack is running
docker compose exec postgres pg_dump -U "$POSTGRES_USER" litellm > backup-$(date +%F).sql
```

## Adding host-side env vars (e.g. provider API keys via env instead of UI)

Two paths:

1. **(Recommended)** Add provider keys through the UI's **Models → Add Model**
   flow; they end up encrypted in Postgres via `LITELLM_SALT_KEY`. No restart
   needed.
2. **For env-style configuration** — add the variable to `.env` and reference
   it from `config/config.yaml` using `os.environ/VAR_NAME`, then add it to the
   `litellm` service `environment:` block in `docker-compose.yml`. Restart the
   `litellm` service.
