# LLM-Proxy Admin UI — v3.4 Design: UI Polish + Model-Identity Fix

**Status:** design (brainstormed 2026-06-09). Builds on the shipped v3 master/servant model (`1.13.1`). Branch: `v3.4-ui-polish` (off `main`).

**Why:** UI-testing feedback surfaced one critical data-model bug, several UX gaps, and a docs need. This round fixes them and adds a comprehensive UI reference guide.

---

## A. Model identity — opaque UUID per deployment *(critical fix)*

**Problem:** `ui_config_applied`/`ui_config_staged` are `PRIMARY KEY(kind, name)`, and model items are keyed `name = model_name`. Two deployments sharing one Public Model Name collide → `ON CONFLICT DO UPDATE` silently overwrites the first. This makes the data model unable to represent LiteLLM's load-balancing pattern (N deployments under one `model_name` = a routing group). LiteLLM's own UI keys deployments by a UUID "Model ID" and shares the public name.

**Design:** a model item's identity becomes an **opaque UUID**; `model_name` moves into `data`.

- **Item shape:** `kind='model'`, `name=<uuid4>`, `data={ model_name, litellm_params, model_info }`.
- **`config_import.split_config`:** for each `model_list` entry, generate a `uuid4()` as the item name; `data = {model_name: m['model_name'], litellm_params: {...}, model_info: {...}}`. (Random ids are fine — the round-trip property holds because render reads `data.model_name`, not the item name. Ids stabilize after the first bootstrap import, which is idempotent via `seed_applied`.)
- **`config_render.render_config`:** the `model_list` entry is `{ 'model_name': data['model_name'], 'litellm_params': data['litellm_params'], ...(model_info) }` — sourced from `data`, NOT the item name. Deleted items excluded as today.
- **Frontend `Models.svelte`:** rows keyed by item `name` (uuid), display `data.model_name`; **duplicate `model_name` is now allowed** (distinct uuids). Add → `crypto.randomUUID()` as the item name, `stageItem('model', id, {model_name, litellm_params, model_info})`. Delete/undo → by uuid. Edit (future) by uuid. Flag rendering unchanged.
- **Migration (idempotent, in bootstrap):** a v3.3 DB may hold model items keyed by `model_name` with `data` lacking `model_name`. On startup, for each applied/staged model item where `data` has no `model_name` key: set `data.model_name = name`, re-insert under a fresh `uuid4` name, delete the old row. Wrapped in a transaction; no-op when already migrated (every model item has `data.model_name`). (The live host's DB currently has no model items, so this is forward-safety.)

**Round-trip guarantee preserved:** `split_config(render_config(items))` is still exact at the `config.yaml` level (model_list entries identical; only the DB item key differs).

---

## B. Provider picker redesign + dark-mode fix (folds in feedback c + d)

**Problem:** the Add-Model provider picker is a `<datalist>` showing raw slugs; mode options show raw `chat`/`audio_speech`. The native datalist popup also renders transparent/unreadable in dark system mode until reclicked (d).

**Design:** replace the `<datalist>` with a styled **`<select>`** of provider **display names** (the Provider-Keys pattern) — a real `<select>` renders correctly in dark mode, fixing (d) structurally.

- **Provider `<select>`:** options are `display_name` (value = slug), sourced from `store`-loaded `catalogProviders()` with `FALLBACK_PROVIDERS` cold-start, pinned-then-alpha order (as today). No logos (decided).
- **Mode `<select>`:** descriptive labels via a new `MODE_LABELS` map in `providers.js`:
  `chat→"Chat Completions"`, `embedding→"Embeddings"`, `completion→"Text Completion"`, `image_generation→"Image Generation"`, `audio_transcription→"Audio – Transcription"`, `audio_speech→"Audio – Speech"`, `rerank→"Rerank"`, `moderations→"Moderations"`, `responses→"Responses"`. The option `value` stays the raw mode; the label is descriptive. Filtered to the provider's catalog modes as today; tooltip notes "the endpoint type used for the health check".
- The provider-specific special fields, prefix affix, Test connection, and catalog auto-fill are preserved.

---

## C. Cost per 1M tokens (feedback e)

**Problem:** cost fields are per-token; the industry norm (and provider pricing tables) is **$ / 1M tokens**.

**Design:** the UI is a units/display layer; `litellm_params` and the pricing catalog stay per-token.

- **Labels:** "Input cost ($ / 1M tokens)", "Output cost ($ / 1M tokens)".
- **On save:** `litellm_params.input_cost_per_token = Number(input_cost_1m) / 1e6` (and output). Omit when blank.
- **Catalog auto-fill:** display value = `per_token * 1e6` (so "$2.50 / 1M").
- **Models table Costs column:** show `In: $X.XX  Out: $Y.YY  / 1M` (per-token × 1e6).

---

## D. Caching stats panel (feedback a)

**Problem:** the Caching screen is bare; it should show live Valkey/Redis health + stats.

**Design:** a new read-only backend endpoint feeds a stats panel.

- **Backend `GET /api/cache/stats`** (auth-gated): connect to Valkey using `REDIS_HOST`/`REDIS_PORT` (add these env vars to the UI container in compose; default `valkey`/`6379`), time a `PING` for `rtt_ms`, run `INFO` (+ `MEMORY STATS`), and return:
  `{ connected: bool, rtt_ms: float, type: 'redis', backend: 'valkey:6379', used_memory, used_memory_peak, used_memory_human, keyspace_hits, keyspace_misses, hit_rate (hits/(hits+misses)), evicted_keys, connected_clients, db_keys (sum of keyspace dbN keys), uptime_in_seconds }`. On connect failure: `{connected: false, error}` (graceful, 200).
  - Dependency: add `redis` (async client) to the UI's `requirements`. INFO/MEMORY STATS are O(1) snapshot commands — negligible cost.
- **Frontend `Caching.svelte`:** keep the existing read-only config (cache on/off, type, backend, TTL from `litellm_setting` items) and ADD a live-stats card: connection dot + RTT, memory (used/peak human), hits/misses + hit-rate %, evictions, key count, connected clients, uptime (humanized). **Refresh policy:** fetch on mount + a manual "Refresh" button + `setInterval` every **10 s while mounted** (cleared on unmount). A small "updated Ns ago" stamp.

---

## E. Configurable proxy port + Dashboard proxy URL (feedback b)

**Problem:** (1) the LiteLLM host port is hard-coded `4000:4000` in compose (the live host carries a manual local port tweak to work around this); (2) users don't know which URL to point clients at.

**Design:** one new env var sets the proxy's host **port**; the **LAN IP/host is auto-detected** (by the setup helper, written to `.env` as an editable default) with an optional override, and the UI assembles `http://{host}:{port}`.

- **New env var `LITELLM_PROXY_PORT`** (default `4000`) — the **host-facing** proxy port. `docker-compose.yml` litellm `ports: ["${LITELLM_PROXY_PORT:-4000}:4000"]`. The container still listens on `4000` internally (`--config … --port 4000` unchanged), so the UI→litellm internal calls (`http://litellm:4000`: test/health/restart) and the in-container healthcheck are unaffected; only the published host port changes. This formalizes the host's existing tweak into config. Added to `.env.example` (default `4000`) and the UI container env.
- **New env var `LITELLM_PROXY_HOST`** (optional, default empty) — the LAN IP/host to advertise to clients. **Auto-detected, overridable:** `setup_env_helper.sh` detects the host's primary LAN IP (it runs on the host: `ip route get 1.1.1.1 | grep -oP 'src \K\S+'`, fallback `hostname -I | awk '{print $1}'`) and offers it as the default; the operator can accept, edit (e.g. a reverse-proxy domain), or clear it. Added to `.env.example` (commented, blank default) and the UI container env.
- **`setup_env_helper.sh`:** prompt for `LITELLM_PROXY_PORT` (default 4000) and `LITELLM_PROXY_HOST` (default = detected LAN IP); preserve existing values on re-run (as the helper already does).
- **Backend:** `GET /api/proxy-info` → `{ proxy_port, proxy_host }` (`proxy_host` = `LITELLM_PROXY_HOST` or `null` when unset).
- **Frontend `Dashboard.svelte`:** a "Proxy endpoint" card that assembles the URL as
  `host = proxy_host || location.hostname` ; `base_url = `${location.protocol}//${host}:${proxy_port}`` — i.e. use the detected/configured LAN IP from `.env`, falling back to the host the admin reached the UI on. Shows **Base URL** + **OpenAI SDK `base_url`** (`…/v1`), each with a copy button, plus a one-line `curl`/SDK hint. (LiteLLM's OpenAI-compatible routes live under `/v1`; the base form is what the OpenAI SDK `base_url` expects.)

---

## F. Comprehensive UI reference docs (request 1)

**New file `docs/admin-ui-guide.md`** — a field-level reference, one section per screen, each setting documented as *what it expects* and *what it does*:

- **Dashboard** — KPI cards (Proxy health, Models count, Virtual keys, Spend 30d, Cache), the **Proxy endpoint** card, the Apply/Discard bar.
- **Usage & Spend** — spend by key/model, date range.
- **Configuration › Models** — per field: **Provider** (the LiteLLM provider/prefix), **Public Model Name** (the name clients request; may repeat to form a load-balancing group), **LiteLLM Model Name / Provider model id** (what LiteLLM sends upstream), **Credential** (a saved Provider Key, or env var), **Mode** (health-check endpoint type), **Upstream API Base** (custom/self-hosted), special fields (Azure api_version, Bedrock region, Vertex project/location), **Input/Output cost ($/1M)**, **Test connection**, health dot, staged flags.
- **Configuration › Routing** — strategy (enum + meanings), num_retries, timeout, cooldown_time, allowed_fails, retry_after, fallbacks (JSON).
- **Configuration › Caching** — read-only config + the live stats panel (each stat explained).
- **config.yaml (rendered preview)** — what it shows; secrets redacted; DB-authoritative note.
- **Access › Virtual Keys** — create (alias, models, budget, expiry), revoke.
- **Access › Provider Keys** — credential name, provider, key (encrypted, `***`), staged flags.
- **System › Housekeeping** — what it prunes; interval.
- **System › Settings** — passthrough/advanced YAML editor, catalog sync, dark mode, export.
- **Apply / Discard model** — staged items, what Apply does (render→validate→write→restart→verify→fold), Discard.

(This guide complements `docs/admin-ui.md`, which stays architecture-focused.)

---

## Build phasing (one branch `v3.4-ui-polish`, released as `1.14.0`)

1. **Model-identity (A)** — backend (import/render/migration) TDD + frontend Models rekey. *(highest risk; do first)*
2. **Provider picker + modes + dark-mode (B)** + **cost per 1M (C)** — `providers.js` + `Models.svelte`.
3. **Caching stats (D)** — backend endpoint + `redis` dep + compose env; `Caching.svelte` panel.
4. **Dashboard proxy URL (E)** — env var (compose/.env/helper) + `/api/proxy-info` + `Dashboard.svelte`.
5. **Docs (F)** — `docs/admin-ui-guide.md`.
6. **Integration verify** on the host UI (`10.0.20.75:8081`) + screenshots; merge → release `1.14.0`.

## Out of scope
- Editing an existing model in place (today: delete + re-add). Could be a later add (the uuid identity makes it clean).
- Provider logos (decided against).
- Recovering the host's pre-reset config (separate, pending an external backup).

## Testing
- **Backend (TDD):** import assigns uuids + `data.model_name`; render emits `model_name` from data; **two model items with the same `model_name` both render** (the bug's regression test); migration rekeys legacy items idempotently; `/api/cache/stats` parses INFO (mock redis); `/api/proxy-info` returns base + `/v1`.
- **Frontend:** build green; Playwright on the host UI — add two models with the same Public Model Name (both persist, no overwrite); provider `<select>` readable in dark mode; mode labels descriptive; cost shown/saved as /1M; caching stats render + refresh; dashboard URL card.
