# LLM-Proxy Admin UI

> **Per-screen field reference (what each setting expects and does):** see
> [`admin-ui-guide.md`](admin-ui-guide.md).

A purpose-built, Apple-HIG admin UI for this LiteLLM proxy stack — a reliable
replacement for the bundled LiteLLM UI.

> **Status: v3 — Master/Servant config redesign** (Phases 1–5 + v2 + v3). The full
> feature set — dashboard, models, provider keys, routing, caching, virtual keys,
> housekeeping, settings — is live and released to GHCR. v3 adds the DB-authoritative
> staged-item config model, encrypted-in-DB credentials, a passthrough editor, and a
> rendered-config preview. Design: the
> [v3 spec](superpowers/specs/2026-06-08-llm-proxy-ui-v3-master-servant-config.md),
> [v1 spec](superpowers/specs/2026-06-07-llm-proxy-ui-design.md),
> [v2 spec](superpowers/specs/2026-06-07-llm-proxy-ui-v2-design.md), and the
> per-phase [plans](superpowers/plans/). Screenshots: [`../README.md`](../README.md).

## Why it exists

The bundled LiteLLM UI was unreliable on this stack:

- Configuring the Redis cache via the UI injected an `ssl` key → LiteLLM bug
  **#10949** (SSLConnection even when `ssl: false`) → TLS handshake against
  plain Valkey hung → endless "Timeout connecting to server".
- The **Router Settings page has no save endpoint** — routing-strategy changes
  never persisted.
- With `store_model_in_db: true`, the **DB silently overrode `config.yaml`**,
  making the effective config opaque.

This UI fixes all three by design: guardrails baked into the render pipeline, and
a DB-authoritative config model with no hidden overrides.

## Architecture — the Master/Servant model

### Roles

- **Master = the UI app + its Postgres DB.** The DB owns *intent* — the desired
  configuration. It is the single source of truth for all settings the UI manages.
- **Servant = LiteLLM.** It owns *execution* — it serves whatever the Master
  dispatches. It stays in config-only mode (`store_model_in_db: false`).
- **`config.yaml` is a rendered artifact.** The Master renders and writes it on
  every Apply. A hand-edit to the file is a scribble the Master overwrites on the
  next Apply. The file is neither the truth nor a store.

Every behavior follows from ownership: "pending" is the Master's set of un-dispatched
intent (staged items); "passthrough" is the part of the order the Master writes
free-form (YAML-validated so the Servant doesn't choke); a failed restart is
reported, not rolled back, because rolling back the file would desync it from the DB.

### Data model — items and tables

A config *item* is the unit of staging. Items are typed by `kind`:

| kind | name (identifier) | data |
|---|---|---|
| `model` | model_name | `{litellm_params, model_info}` |
| `credential` | credential_name | `{provider, value_encrypted}` (Fernet) |
| `router_setting` | the key (e.g. `routing_strategy`) | the value |
| `litellm_setting` | the key (e.g. `cache`, `cache_params`) | the value |
| `general_setting` | the key (e.g. `background_health_checks`) | the value |
| `passthrough` | `_` (singleton) | the raw extra-YAML dict |

Two Postgres tables (`ui_` prefix):

- **`ui_config_applied`** `(kind, name, data, updated_at)` — the last-applied state;
  mirrors what `config.yaml` holds.
- **`ui_config_staged`** `(kind, name, data, flag, updated_at)` — pending intent;
  `flag` is `new` / `changed` / `deleted`.

**Applied** = the dispatched truth. **Staged** = pending: `new` = absent from
applied; `changed` = exists in applied with new data; `deleted` = exists in applied,
marked for removal (the applied row stays until Apply, so deleted items appear
struck-through in the UI until then).

The **effective view** = applied, overlaid by staged: `new`→add, `changed`→replace,
`deleted`→keep but mark struck-through. Each item carries its flag (or none = clean)
so the UI colors `new`/`changed` and strikes `deleted`.

Pending state is **DB-backed** — it survives logout and restart.

### The render

`render(effective)` assembles a `config.yaml` dict:
1. Group non-deleted effective items by kind → `model_list`, `router_settings`,
   `litellm_settings`, `general_settings`, `credential_list` (credentials
   **decrypted** to literal `api_key` here only).
2. Deep-merge the `passthrough` item for any top-level keys the UI doesn't model
   (managed sections always win).
3. **Validate** through guardrails + schema (`routing_strategy` enum, no `ssl` in
   `cache_params`, required model fields, no literal secrets except the materialized
   `credential_list`). Invalid render → 422, nothing written.
4. Serialize to YAML.

### Save / Apply / Discard

**Save (per item, per screen):** upsert into `ui_config_staged` with the right flag
(compare to applied to compute `new` vs `changed`; a UI delete writes flag
`deleted`). No file write, no restart. Returns the new pending count.

**Apply — the commit boundary is a successful, read-back file write; there is no
rollback after it:**

1. `render(effective)` → **validate**. Invalid → **422**, nothing written, staged
   intact (abortable before the commit point).
2. **Backup** the current file (`config.yaml.bak.*`, 0600) for inspection.
3. Write rendered YAML to a temp file; **read it back** to confirm bytes on disk.
   Disk/readback failure → **500**, nothing folded, staged intact (still abortable).
4. **COMMIT:** `os.replace` temp → `config.yaml`; `chmod 0600`; **fold** staged into
   applied (`new`/`changed` upsert, `deleted` rows removed from applied); **clear**
   staged. The invariant now holds: `config.yaml == render(applied)`, staged empty.
5. **Restart** the Servant; **verify** health + `/v1/models`.
6. The restart/verify result is **reported, not rolled back**: healthy →
   `{applied:true, servant:"healthy"}`; unhealthy → `{applied:true,
   servant:"unhealthy", detail}`. The config is committed and consistent — a valid
   config the Servant still rejects at runtime is an operational issue fixed forward
   (correct the setting in the UI and re-apply). The backup file is retained for
   inspection. **No auto-revert, no last-good snapshot — by design**, to keep
   `file == DB` always true.

**Discard:** `DELETE FROM ui_config_staged` (optionally scoped to one item). No file
write, no restart. Pending → empty (or reduced). Truly discards the change — no
ghost re-appearances.

**Drift guard:** if staged is empty but `render(applied)` differs from the on-disk
file (someone hand-edited the file), the UI surfaces a non-blocking notice with
"Re-apply from Master" — overwrites the file from the DB. The file is never
authoritative.

### Credentials

Credentials are just another item kind. Secrets are **Fernet-encrypted at rest** in
`ui_config_*.data` (`SESSION_SECRET`/`CREDENTIALS_KEY` derivation, carried from
v2.2). The UI and all API endpoints return credential values as `***` (redacted).
Only the rendered `config.yaml` (0600, gitignored) holds the materialized literal —
the minimum exposure needed for the Servant to function. A discarded credential add
truly disappears (no ghost in a separate vault table).

### Passthrough (advanced config)

The **Raw / Advanced** editor (Settings screen) is a YAML textarea for top-level
LiteLLM keys the UI doesn't model. It is stored as the `passthrough` item in the
DB. On Apply, it is deep-merged into the render (managed sections win). The backend
YAML-parses and validates it before staging — a bad free-form key is caught before
it can crash the Servant.

### `config.yaml` ownership

`config.yaml` is written by the Master on every Apply. Consequences for operators:

- **Do not treat it as the source of truth.** The DB is. Hand-edits are overwritten
  on the next Apply.
- The file is written **mode `0600`**, gitignored. It holds materialized credential
  secrets. The repo commits `config/config.yaml.example` (secret-free) as the
  bootstrap seed.
- Timestamped backups (`config.yaml.bak.*`) are likewise 0600 and git-ignored.
- The UI never returns credential plaintext to the browser.
- To hand-edit urgently: use the UI (stage + apply), or `sudo`. Keep
  `LITELLM_SALT_KEY`/`SESSION_SECRET`/`CREDENTIALS_KEY` stable — rotating them
  makes stored encrypted credentials undecryptable.

### Bootstrap and v2 → v3 migration

On the first v3 boot, if `ui_config_applied` is empty, the app **imports** the
existing `config.yaml` (or `config.yaml.example` if no live file exists): managed
sections are split into typed items; unknown top-level keys go to the `passthrough`
item; literal `credential_list` secrets are encrypted into `credential` items. The
`config.yaml` already matches, so no rewrite occurs. If upgrading from v2, the old
`ui_credentials` table is migrated into credential items and dropped. The import is
idempotent (guarded by an applied-table-empty check + a migration marker).

### Scope

The staging model governs the `config.yaml` sections: `model_list`,
`router_settings`, `litellm_settings` (incl. caching), `general_settings`,
`credential_list`, and passthrough. **Virtual keys, budgets, and spend** stay on
LiteLLM's runtime API — they are operational state created live with no restart,
not part of the staged config tables.

## Running it

The UI runs as the `llm-proxy-ui` service in the root
[`docker-compose.yml`](../docker-compose.yml), pulled from GHCR (pinned to an
immutable release tag, e.g. `ghcr.io/tekgnosis-net/llm-proxy-ui:1.19.1`) — no
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
  count, 30-day spend, cache on/off. The amber Apply bar appears at the top whenever
  staged items are pending.
- **Usage & Spend** — total spend, by model, by key, daily activity (last 30d).
- **Models** — catalog-driven provider picker (auto-fill model pricing/context/mode
  from synced `provider_endpoints_support.json`); add/edit with credential dropdown,
  mode/endpoint, custom input/output costs, pre-save **Test connection**, and a
  per-model health dot. Items show `new` / `changed` / `deleted` flags; saves stage
  into `ui_config_staged`.
- **Provider Keys** — add/list-masked/delete provider credentials; all staged
  (items flagged `new` shown until Apply). Secrets encrypted at rest in DB; returned
  as `***` everywhere except the rendered `config.yaml`.
- **Routing** — strategy (valid enum only), retries, timeout / cooldown /
  `allowed_fails` / `retry_after`, fallbacks. The staged ● badge + pending count
  appear on changed items; the Apply bar shows the total.
- **Caching** — **read-only** status panel (effective `valkey:6379`); the cache
  backend is provisioned in `docker-compose.yml`, not edited here.
- **Rendered config preview** — `GET /api/config/rendered`: the would-be
  `config.yaml` as the Master would write it on the next Apply (credential values
  redacted). A "preview the dispatch" view — not a source-of-truth editor.
- **Virtual Keys** — create (one-time plaintext shown once) / list / delete with
  budgets, model allowlist, expiry. (Runtime API; not in the staged config tables.)
- **Housekeeping** — DB stats + maintenance.
- **Settings** — **Raw / Advanced (passthrough)** YAML editor for LiteLLM keys the
  UI doesn't model (staged as the `passthrough` item; YAML-validated before staging);
  **LiteLLM catalog sync** (last-synced + "Sync now"); dark mode toggle.

### The Apply bar and Discard

The global **Apply** bar (top) appears whenever `ui_config_staged` is non-empty
(DB-backed, so it's correct after a fresh login). It shows the pending-item count
and two actions:

- **Apply** — render → validate → write `config.yaml` → fold staged into applied →
  clear staged → restart proxy → verify health. Reports healthy or unhealthy; the
  config is committed either way.
- **Discard** (with confirmation) — clears staged, no file write, no restart.

Individual items can also be discarded per-item from their screen.

## API (config endpoints)

All login-gated; credential secrets stay server-side.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config/state` | Effective items grouped by kind, each with `flag`; credential values redacted; `pending` bool + counts |
| `PUT` | `/api/config/item` | Stage one item `{kind, name, data}` (backend computes `new`/`changed`) |
| `DELETE` | `/api/config/item/{kind}/{name}` | Stage a `deleted` flag |
| `GET` | `/api/config/passthrough` | The passthrough item (redacted) |
| `PUT` | `/api/config/passthrough` | Stage raw YAML → parsed, validated, staged as passthrough item |
| `GET` | `/api/config/rendered` | The would-be rendered `config.yaml` (credential values redacted) |
| `POST` | `/api/apply` | render+validate+write+readback+fold+clear+restart+verify → `{applied, servant, detail?}` |
| `POST` | `/api/discard` | Clear staged (optional `?kind=&name=` to discard one item) |

Apply HTTP codes: **200** (committed, servant healthy or unhealthy both return 200
with `servant` field); **422** invalid render (pre-commit, nothing written); **500**
disk/readback failure (pre-commit, nothing folded).

## Guardrails (the old bugs can't recur)

- The render pipeline **never writes an `ssl` key** into `cache_params` → #10949
  is impossible.
- `routing_strategy` is constrained to the valid enum (`cost-based-routing`, …);
  `lowest-cost` is rejected at stage time.
- Required model fields are validated at Apply; literal secrets in non-credential
  fields are rejected (only the materialized `credential_list` in the rendered file
  is allowed).
- The passthrough is YAML-parsed and run through the same validate step at stage
  time and at Apply.

## CI/CD

`main` runs **semantic-release** (conventional commits → versions + GitHub
releases) and publishes the UI image to
`ghcr.io/tekgnosis-net/llm-proxy-ui:<version>` + `:latest`.

## Credit

Built on **[LiteLLM](https://github.com/BerriAI/litellm)** (BerriAI) — the proxy
that powers all routing, caching, key management, and spend tracking. This UI is
a front-end + deployment around it. See the repo [README](../README.md) for full
acknowledgements.
