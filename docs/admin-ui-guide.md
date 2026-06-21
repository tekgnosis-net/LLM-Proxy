# LLM-Proxy Admin UI — Per-Screen Field Reference

> **Audience:** operators and end-users running the proxy stack. For the system
> architecture and data-model design, see [`admin-ui.md`](admin-ui.md). This guide
> is a field-level reference — what each setting expects and what it does.

---

## The staged-apply model (read this first)

Every configuration change in the UI is **staged** before it is live. Saving a
field writes it to `ui_config_staged` in Postgres — no file is written and the
proxy does not restart. Changes accumulate until you click **Apply** (or are
discarded). The amber **Apply bar** at the top of every screen shows the pending
count and two actions.

**Staged flags** appear on individual rows:

| Badge | Meaning |
|---|---|
| `new` (blue) | Item exists in staged but not yet in the applied config |
| `changed` (orange) | Item exists in applied; staged version differs |
| `deleted` (red, strikethrough) | Item exists in applied; marked for removal on next Apply |

A staged ● dot next to a field name in Routing means that specific setting is
staged.

---

## Apply / Discard bar

Appears at the top of every screen when `ui_config_staged` is non-empty. The
state is DB-backed and survives logout and page refresh.

### Apply

Executes a multi-step pipeline:

1. **Render** — assembles the effective config (applied items overlaid by staged)
   into a `config.yaml` dict. Credentials are decrypted and materialized as
   literal `api_key` values in the rendered `credential_list` (the only place
   secrets appear in plaintext).
2. **Validate** — guardrails check for invalid fields (e.g. `ssl` in
   `cache_params` is forbidden, `routing_strategy` must be a known enum value).
   If validation fails → HTTP 422, **nothing is written**, staged items are
   intact and still discardable.
3. **Write** — renders YAML to a temp file; reads it back to confirm disk write.
   Failure here → HTTP 500, nothing is folded.
4. **Commit** — `os.replace` temp → `config.yaml` (mode 0600); folds staged
   into applied (`new`/`changed` upsert; `deleted` rows removed from applied);
   clears staged.
5. **Restart** — sends a restart signal to the LiteLLM servant.
6. **Verify** — health-checks the servant + `/v1/models`. Reports healthy or
   unhealthy. **The config is committed either way.** An unhealthy servant after
   a valid Apply is an operational issue (fix the setting in the UI and re-apply).
   No auto-revert — the DB and file are always in sync by design.

### Discard

Clears all staged items (`DELETE FROM ui_config_staged`). No file write, no
restart. Per-item Discard is available on each screen (e.g. the Undo button on a
deleted model row).

---

## Dashboard

The landing screen. Fetches health, usage, and proxy-info on load.

### KPI cards

| Card | Expects | Does |
|---|---|---|
| **Proxy** | — | Shows a green/red dot + "Healthy" or "Down" depending on whether the proxy's health endpoint is reachable. Sub-label shows "DB connected" when the servant's DB is connected. |
| **Models** | — | Count of model items currently in the effective (applied+staged) config. |
| **Virtual keys** | — | Count of active virtual keys returned by the LiteLLM keys API. |
| **Spend (30d)** | — | Total spend across all keys for the last 30 days, in USD. Shows `$0.00` if usage data is unavailable. |
| **Cache** | — | "on" or "off" depending on the effective `litellm_setting` named `cache`. Sub-label shows the cache type (e.g. `redis`). |

### Proxy endpoint card

Appears only when the `/api/proxy-info` endpoint returns successfully (i.e. when
`LITELLM_PROXY_PORT` is configured). Assembles the proxy base URL from the
server-side `proxy_host` (or the browser's current hostname as fallback) and
`proxy_port`.

| Row | Value | Button |
|---|---|---|
| **Base URL** | `http://<host>:<port>` — the root URL of the proxy | Copy to clipboard |
| **OpenAI SDK `base_url`** | `http://<host>:<port>/v1` — the `/v1` prefix expected by the OpenAI Python SDK and other OpenAI-compatible clients | Copy to clipboard |

Hint beneath: "Point OpenAI-compatible clients at the `/v1` URL with a virtual
key."

**How the host/port are determined:**

- `proxy_port` — set by `LITELLM_PROXY_PORT` in `.env` (default `4000`). This
  is the **host-facing** port (the published Docker port); the container always
  listens internally on `4000`.
- `proxy_host` — set by `LITELLM_PROXY_HOST` in `.env` (optional). When blank,
  the UI falls back to `location.hostname` (the host you opened the admin UI on).
  The `setup_env_helper.sh` script auto-detects your LAN IP and offers it as the
  default.

---

## Usage & Spend

Read-only. Covers the last 30 days. Data comes from LiteLLM's spend tracking
(runtime API, not the staged config).

> **Timezones:** all times and dates on this screen (and in the live **Logs** view)
> are shown in **your browser's local timezone**. The backend stores and emits UTC;
> the conversion happens client-side, so two viewers in different zones each see their
> own local clock.

### Summary cards

| Card | Shows |
|---|---|
| **Total spend** | Aggregate USD spend across all keys for the period |
| **Requests (30d)** | Total API request count |
| **Tokens (30d)** | Total token count (input + output) |

### Spend by model

Horizontal bar chart. Each bar is one model (the public model name the key
called). Bar width is proportional to spend relative to the highest-spend model.
Dollar amount shown at the right.

### Spend by key

Table. Columns: Key alias (or key name), Spend (USD). One row per virtual key
that has spend recorded.

### Daily requests

Sparkbar chart. One column per day; height proportional to request count. Hover
for the exact date and count.

---

## Models

Add and manage LiteLLM model deployments. Each deployment is a mapping from a
**Public Model Name** (what clients request) to a **provider model id** (what
LiteLLM sends upstream). Changes are staged until Applied.

> **Load-balancing groups:** The same Public Model Name may appear in multiple
> rows with different providers or model ids. These rows form a **routing group**
> — LiteLLM treats them as interchangeable deployments and distributes traffic
> according to the active Routing strategy. Each row is stored under a unique
> internal uuid; duplicate public names are valid and expected.

### Add model form fields

| Field | Expects | Does |
|---|---|---|
| **Provider** | Select from list (display names, e.g. "OpenAI", "Azure OpenAI", "AWS Bedrock") | Sets the provider prefix used when building the `litellm_params.model` string (e.g. `openai/`, `azure/`). The dropdown is populated from the synced LiteLLM catalog; falls back to a built-in list on first load or if the catalog is unavailable. Common providers (OpenAI, Anthropic, Azure, Bedrock, Gemini, Vertex AI) are pinned to the top; the rest appear alphabetically. |
| **Public model name** | Free text, e.g. `gpt-4o` | The name clients use in their API requests (`model: "gpt-4o"`). May be repeated across rows to form a routing group. |
| **Provider model id** | Free text with a `<provider>/` prefix shown, e.g. `gpt-4o` → full value `openai/gpt-4o` | The upstream model identifier LiteLLM passes to the provider. The prefix is prepended automatically from the selected Provider. Blur or "Look up pricing" triggers a catalog lookup to auto-fill costs and mode. |
| **Look up pricing** button | Requires Provider model id to be filled | Queries the synced LiteLLM catalog for this model. If found, auto-fills Input cost, Output cost, and Mode. Shows "auto-filled from catalog" on success; leaves fields blank if not in catalog. |
| **Credential** | Select a saved Provider Key by name, or "— env var / none —" | If a saved credential is selected, the model uses it (no env var needed; the credential is resolved at render time). If "none", the API key env var field appears instead. |
| **API key env var** | Visible only when Credential is "none". Free text, e.g. `OPENAI_API_KEY` | The OS environment variable name holding the real API key. Stored as `os.environ/OPENAI_API_KEY` in `config.yaml` — no literal secret in the config. Set the real value in `.env`. |
| **API version** | Visible for **Azure OpenAI** only. Free text, e.g. `2024-02-15-preview` | Passed as `api_version` in `litellm_params`. Required for Azure deployments. |
| **AWS region** | Visible for **AWS Bedrock** only. Free text, e.g. `us-east-1` | Passed as `aws_region_name` in `litellm_params`. |
| **Vertex project** | Visible for **Google Vertex AI** only. Free text, e.g. `my-gcp-project` | Passed as `vertex_project` in `litellm_params`. |
| **Vertex location** | Visible for **Google Vertex AI** only. Free text, e.g. `us-central1` | Passed as `vertex_location` in `litellm_params`. |
| **Mode** | Select from list with descriptive labels | Sets the `model_info.mode` — the endpoint type used for the health check. Options and their labels: |

Mode option values and labels:

| Value | Displayed as |
|---|---|
| `chat` | Chat Completions |
| `embedding` | Embeddings |
| `completion` | Text Completion |
| `image_generation` | Image Generation |
| `audio_transcription` | Audio – Transcription |
| `audio_speech` | Audio – Speech |
| `rerank` | Rerank |
| `moderations` | Moderations |
| `responses` | Responses |

The dropdown is filtered to modes the provider supports (from the catalog); the full
list above is the fallback when catalog data is unavailable.

| Field | Expects | Does |
|---|---|---|
| **Advanced: custom endpoint** (toggle) | Click the ▸ link to expand | Reveals the API base field for self-hosted or custom-endpoint deployments. Also always shown for providers that require it (Azure, OpenAI-compatible, vLLM). |
| **API base (override / self-hosted)** | URL, e.g. `https://your-endpoint/v1`. Leave blank for managed providers. | Passed as `api_base` in `litellm_params`. LiteLLM resolves the URL from the provider prefix for managed providers; only set this for self-hosted or custom deployments. |
| **Input cost ($ / 1M tokens)** | Non-negative number; blank = use LiteLLM's built-in catalog price | The input token cost in US dollars per **1 million** tokens. Stored internally as per-token (`÷ 1e6`) in `litellm_params.input_cost_per_token`. Used for spend tracking. |
| **Output cost ($ / 1M tokens)** | Non-negative number; blank = use LiteLLM's built-in catalog price | The output token cost in US dollars per **1 million** tokens. Stored internally as per-token in `litellm_params.output_cost_per_token`. |
| **Disable background health check** (checkbox) | Tick to exclude this deployment from the periodic background health probe | Sets `model_info.disable_background_health_check`. Recommended for paid providers (e.g. deepinfra), where the background check sends a real, billed request each interval — use the **Check now** button on demand instead. Honored once `general_settings.health_check_skip_disabled_background_models` is enabled (the UI stages that global flag automatically the first time you disable a model). In hybrid mode this is a `model_info` change, so it converges via the PATCH update path and is what the **content-aware drift** detector watches. |
| **Test connection** button | Requires Public model name + Provider model id | Sends a test inference request to the provider using the current form values (without saving). Reports "Connection successful" or the error. Does not stage anything. |
| **Save** button | Requires Public model name + Provider model id | Stages the model as a `new` item (uuid identity) in `ui_config_staged`. The model appears in the table with a `new` badge. Takes effect on Apply. |

### Models table columns

| Column | Shows |
|---|---|
| **Model name** | The Public Model Name. Struck through (grey) if the row is staged for deletion. |
| **litellm model** | The full `litellm_params.model` string (e.g. `openai/gpt-4o`). This is what LiteLLM uses when calling the upstream API. |
| **Costs** | Input and output cost in `$ / 1M` tokens (converted from the stored per-token values). Shows "—" if no costs are set. |
| **Health** | A dot: green = healthy (last health check passed), red = unhealthy, grey = unknown (no health data yet). |
| **Check** | **Check now** button — runs an on-demand health check for that one deployment and shows the result inline. Useful for deployments whose background check is disabled. |
| **Status** | `new` / `changed` / `deleted` badge if the row has a staged change; blank if clean. |
| **Action** | **Edit** button (re-opens the form to change the deployment in place, re-staged under the same uuid). **Delete** button (stages a `deleted` flag; row stays with strikethrough until Apply). **Undo** button (visible when `deleted`; discards the deletion). |

### Drift & Resync (hybrid mode only)

When the proxy runs in hybrid hot-apply mode (`STORE_MODEL_IN_DB=true`), the Models
header shows a **drift badge** comparing the UI's applied models against the proxy's
live deployments:

- **In sync ✓** — the live proxy matches the UI's intent.
- **⚠ N out of sync** — they differ. Drift is **content-aware**: it counts models
  **missing** from the proxy, **extra** deployments the UI no longer wants, and models
  present on both sides whose **`model_info` content differs** (e.g. the background
  health-check toggle). Only UI-managed fields are compared, so litellm's own derived
  fields never trigger a false warning.

A **Resync to proxy** button appears when out of sync. It shows a confirmation preview
of the plan — `+ add` (missing), `~ update` (content-drifted), `- delete` (extras) —
and, on confirmation, converges the live proxy to the UI's applied config **hot, with no
restart**: it adds missing models, PATCHes drifted ones, and removes extras, restoring
each by its original `model_info.id`. Deletions happen only after you confirm.

Normal **Apply** pushes only your staged edits; **Resync** is the on-demand
full-convergence action for when the live proxy has drifted out-of-band (a direct API
call, a hand-edit, or a failed partial apply).

---

## Routing

Per-field saves (each field has its own Save/Reset buttons). Changes stage
immediately; a ● dot appears next to the field name when it is staged. All
settings take effect on Apply.

| Field | Expects | Does |
|---|---|---|
| **Routing strategy** | Select one of the six strategies below | Controls how LiteLLM picks a deployment when multiple deployments share the same Public Model Name (a routing group). Stored as `routing_strategy` in `router_settings`. |
| **Num retries** | Non-negative integer; default 3 | Number of times LiteLLM retries a failed request before giving up. Stored as `num_retries`. |
| **Timeout (s)** | Non-negative number; default 600 | Maximum seconds to wait for a response from the upstream provider before timing out. Stored as `timeout`. |
| **Cooldown time (s)** | Non-negative number | After a deployment exceeds `allowed_fails` errors in a minute, it is excluded from routing for this many seconds. Stored as `cooldown_time`. |
| **Allowed fails** | Non-negative integer | Number of errors per minute a deployment may return before it is put into cooldown. Stored as `allowed_fails`. |
| **Retry after (s)** | Non-negative number | Minimum seconds to wait before retrying a failed deployment. Stored as `retry_after`. |
| **Fallbacks** | Valid JSON array, e.g. `[{"gpt-4": ["gpt-4o"]}]` | Maps a model name to one or more fallback model names. If the primary model fails all retries, LiteLLM tries the fallbacks in order. Must be valid JSON; the UI shows a parse error otherwise. Stored as `fallbacks`. |

### Routing strategy options

| Value | Behavior |
|---|---|
| `simple-shuffle` | Pick a deployment at random from the routing group (default). |
| `least-busy` | Prefer the deployment with the fewest active requests. |
| `usage-based-routing` | Route based on historical token usage — avoids overloading rate limits. |
| `usage-based-routing-v2` | Updated usage-based algorithm (improved accuracy). |
| `latency-based-routing` | Prefer the deployment with the lowest recent response latency. |
| `cost-based-routing` | Prefer the cheapest deployment in the group (based on `input_cost_per_token` / `output_cost_per_token`). Note: the value `lowest-cost` is **not valid** and is rejected by the validator. |

**Save / Reset buttons (per field):** Save stages the current value. Reset
discards the local edit back to the last staged (or applied) value without saving.

---

## Caching

Read-only status panel. The cache backend is provisioned in `docker-compose.yml`
(the `valkey` service); it is not configurable from this screen.

### Config panel (static)

| Row | Shows |
|---|---|
| **Status** | "Enabled" (green) or "Disabled" (grey) — from the effective `litellm_setting` named `cache`. |
| **Type** | Cache type from `cache_params.type` (e.g. `redis`). |
| **Backend** | Always `valkey : 6379` — the fixed Docker DNS address of the Valkey service. |
| **TTL** | Time-to-live in seconds from `cache_params.ttl`; shows "default (600 s)" when not set. |

Note on backend config: the `host` and `port` values in `cache_params` are
`os.environ/` references resolved by Docker at runtime (effective: `valkey:6379`).
To change the backend, edit `docker-compose.yml`.

### Live Stats panel

Fetches from `/api/cache/stats` (which runs `PING` + `INFO` against Valkey) on
mount and every **10 seconds** while the screen is open. A manual **Refresh**
button triggers an immediate fetch. An "updated Ns ago" stamp shows staleness.

| Stat | Explains |
|---|---|
| Connection dot + RTT | Green dot = connected. Red dot = disconnected. RTT (round-trip time in ms) is the time for a `PING` command to return — a measure of network latency to the cache. |
| **Backend label** | The host:port the UI is connected to (e.g. `valkey:6379`). |
| **Used memory** | RAM currently allocated by Valkey (human-readable, e.g. `4.50M`). |
| **Peak memory** | Highest RAM Valkey has used since startup. Useful for sizing. |
| **Cache hits** | Total number of successful cache lookups since Valkey started. |
| **Cache misses** | Total cache lookups that found no entry (request had to go upstream). |
| **Hit rate** | `hits / (hits + misses)` expressed as a percentage. Higher is better; a rising rate means the cache is increasingly effective. |
| **Evictions** | Number of keys Valkey has evicted (deleted early to free memory). A non-zero value means the cache is under memory pressure — consider increasing `maxmemory` in `docker-compose.yml`. |
| **Key count** | Total number of keys currently stored across all Valkey databases. |
| **Connected clients** | Number of client connections currently open to Valkey (includes the UI's own connection). |
| **Uptime** | How long Valkey has been running (format: `Xd Yh`). A recent restart (low uptime) explains a low hit rate. |

When disconnected, the panel shows a red dot and the error message returned by the
connection attempt.

---

## config.yaml (rendered preview)

Navigation label: **config.yaml**. Read-only.

Shows the JSON representation of the `config.yaml` dict that **would be written to
disk on the next Apply** — assembled from the current effective config (applied
items overlaid by staged items).

- **Credential values are redacted** — shown as `"***"` in the preview; the actual
  secrets are written to the real file on Apply.
- **DB-authoritative:** the preview reflects the DB state, not the on-disk file.
  If someone has hand-edited `config.yaml` since the last Apply, the preview will
  differ from the file (the DB wins on the next Apply).
- The preview updates on each page load; it does not auto-refresh.

Use this screen to verify your staged changes look correct before clicking Apply.

---

## Virtual Keys

Virtual keys are LiteLLM runtime objects — they are **not** part of the staged
config and do not require Apply. Create and delete operations take effect
immediately via the LiteLLM API.

> **Important:** A newly created key is shown **once** in a banner immediately
> after creation. Copy it now — it cannot be retrieved again.

### Create key form

| Field | Expects | Does |
|---|---|---|
| **Alias** | Free text, e.g. `ci-pipeline` (optional) | A human-readable label for the key. Shown in the key list and spend reports. |
| **Models** | Multi-select from the list of configured model names (hold Ctrl/Cmd to select multiple; select none = all models allowed) | Restricts which public model names this key may call. A key with no models selected can call any model. |
| **Max budget ($)** | Non-negative number, e.g. `50` (optional) | Maximum USD spend allowed for this key. When reached, further requests are rejected. |
| **Budget resets** | Duration string, e.g. `30d`, `7d` (optional) | How often the spend counter resets. Leave blank for a one-time lifetime budget. |
| **Expires** | Duration string, e.g. `30d`, `90d`, blank = never (optional) | When the key expires. After expiry, requests using it are rejected. |
| **RPM limit** | Non-negative integer (optional) | Maximum requests per minute for this key. |
| **TPM limit** | Non-negative integer (optional) | Maximum tokens per minute for this key. |

### Key list columns

| Column | Shows |
|---|---|
| **Alias** | The key alias, or "—" if none was set. |
| **Models** | The allowed model names, comma-separated; "all" if unrestricted. |
| **Spend / budget** | Current spend vs. budget (e.g. `$1.23 / $50.00`), or just current spend if no budget. |
| **Expires** | Expiry date in locale format, or "never". |
| **Action** | **Delete** button — permanently revokes the key. A confirmation dialog is shown. Requests using the key will stop working immediately. |

---

## Provider Keys

Provider Keys (credentials) are saved API keys for upstream providers. They are
stored **Fernet-encrypted at rest** in the Postgres DB and are **never returned in
plaintext** to the browser (shown as `***` in the UI). The actual key value is
written to `config.yaml` (mode 0600, gitignored) only at Apply.

Changes are staged and take effect on Apply.

### Add key form

| Field | Expects | Does |
|---|---|---|
| **Name** | Free text, e.g. `openai_prod` | A unique credential identifier. Models reference this name via the Credential dropdown on the Models screen. |
| **Provider** | Select from the provider list (same catalog-driven list as Models) | Associates the credential with a provider for labeling purposes. |
| **API key** | The real API key, e.g. `sk-…` (password input) | The secret value. Fernet-encrypted before being stored in `ui_config_staged`. Never stored or returned in plaintext. |

### Credential list columns

| Column | Shows |
|---|---|
| **Name** | The credential name. Struck through if staged for deletion. |
| **Provider** | The associated provider name. |
| **Value** | Always `***` — the key is never shown. |
| **Status** | `new` / `changed` / `deleted` badge if staged; blank if clean. |
| **Action** | **Delete** (stages a `deleted` flag). **Undo** (visible when `deleted`; discards the staged deletion). |

---

## Housekeeping

Database maintenance. No staged-config interaction — operations run immediately.

### Database card

Shows the Postgres database size and a table of row counts per `ui_*` table.
Useful for understanding how much data has accumulated.

### Maintenance card

| Item | Shows |
|---|---|
| **Scheduled cron** | "every Nh" if enabled, or "disabled". Controlled by `HOUSEKEEPING_ENABLED` and `HOUSEKEEPING_INTERVAL_HOURS` env vars. |
| **Retention** | The retention window in days (`HOUSEKEEPING_RETENTION_DAYS`). Spend logs older than this are pruned. |
| **Delete expired keys** | "yes" or "no" (`HOUSEKEEPING_DELETE_EXPIRED_KEYS`). When yes, virtual keys past their expiry date are removed on each maintenance run. |

**Run maintenance now** button: immediately executes the same maintenance the
cron would run — trims spend logs older than the retention window and optionally
deletes expired keys. A confirmation dialog is shown. Result banner shows how many
spend log rows were trimmed and how many expired keys were deleted.

To configure the schedule, set `HOUSEKEEPING_*` environment variables in `.env`
and restart the stack.

---

## Settings

### Appearance

| Control | Does |
|---|---|
| **Dark mode** checkbox | Toggles between light and dark theme. Persists in `localStorage`. Affects all screens. |

### Raw / Advanced (passthrough)

A YAML textarea for LiteLLM configuration keys the UI does not model (e.g.
`callbacks`, `guardrails`, custom middleware). This content is stored as the
`passthrough` item in `ui_config_staged` and, on Apply, is **deep-merged** into
the rendered `config.yaml` — managed sections (models, routing, caching,
credentials) always win over passthrough keys of the same name.

| Control | Expects | Does |
|---|---|---|
| **Textarea** | Valid YAML; top-level keys only, e.g. `callbacks:\n  - langfuse` | Staged as the passthrough item. Must be valid YAML — the backend parses and validates before staging; a syntax error is returned as a 422. |
| **Save passthrough** button | — | Submits the textarea content. On success shows "Staged. Click Apply to make it live." Does not restart the proxy. |

### Export config.yaml

| Control | Does |
|---|---|
| **Export config.yaml** link | Downloads a snapshot of the current effective `config.yaml` as rendered by the server (credential values redacted). Useful for inspection or off-site backup. |

### LiteLLM catalog

The catalog holds model pricing, context windows, and provider endpoint metadata,
synced from the LiteLLM repository. It is used by the Models screen to auto-fill
costs and mode when you enter a Provider model id.

| Item | Shows |
|---|---|
| **Last synced** | Timestamp of the most recent successful sync, or "never". |
| **Model / provider counts** | Number of models and providers in the local catalog. |
| **Last error** | If the last sync failed, the error message appears here. |

| Control | Does |
|---|---|
| **Sync now** button | Triggers an immediate catalog sync from the LiteLLM upstream source. Reports how many models and providers were synced. This is a network request; it may take a few seconds. |

---

## Quick reference — field types and conventions

| Convention | Meaning |
|---|---|
| `os.environ/VAR_NAME` in config | API keys are stored as env-var references, not literals. Set the real value in `.env`. |
| Costs in `$ / 1M tokens` | Costs entered in the UI are in dollars per million tokens. Stored internally as per-token (÷ 1e6). Provider pricing pages also quote per 1M tokens, so no conversion is needed when copying from provider docs. |
| Duration strings | Virtual key expiry and budget-reset fields accept strings like `30d`, `7d`, `24h`. Refer to LiteLLM docs for the full format. |
| Staged items survive logout | Pending changes are stored in Postgres. A browser refresh or logout does not lose staged changes. |
| Apply bar count | Shows how many items are currently staged (pending). One "item" is one logical setting (e.g. one model row, one routing field, one credential). |
