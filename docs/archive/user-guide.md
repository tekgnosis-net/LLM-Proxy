# LLM-Proxy — User Guide

A practical, UI-first guide for getting work done in this LiteLLM
deployment. LiteLLM's official docs lean heavily on `config.yaml`; this guide
focuses on the admin UI at <http://localhost:4000/ui> and tells you when you
*do* need to drop down to YAML.

---

## Quick start (5 minutes)

1. **Bring the stack up** (if it isn't already):

   ```bash
   docker compose up -d
   docker compose ps   # wait for all three services to show (healthy)
   ```

2. **Open the UI** at <http://localhost:4000/ui>.

3. **Log in.** Username `admin`, password = the value of
   `LITELLM_MASTER_KEY` in `.env`.

4. **Add your first model.** Sidebar → **Models** → **Add Model** →

   | Field             | Value                                |
   |-------------------|--------------------------------------|
   | Provider          | `OpenAI` (or your provider of choice)|
   | LiteLLM Model Name| `openai/gpt-4o-mini`                 |
   | Public Model Name | `gpt-4o-mini` (what your clients send)|
   | API Key           | your OpenAI key (e.g. `sk-proj-…`)   |

   Click **Add Model**. The model is now persisted in Postgres and
   immediately available — no restart needed (this is the
   `STORE_MODEL_IN_DB=true` magic).

5. **Test it.** Sidebar → **Test Key** → pick the model you just added →
   send "Hello, world." You should get a response in a few seconds.

6. **Create a virtual key for your client app.** Sidebar →
   **Virtual Keys** → **+ Create New Key** → name it (e.g.
   `my-laptop-cli`), restrict to specific models if you like, **Create**.
   Copy the `sk-…` key that appears — **this is the only time it's shown
   in plaintext**.

7. **Use the proxy from any OpenAI-compatible client:**

   ```bash
   curl http://localhost:4000/v1/chat/completions \
     -H "Authorization: Bearer sk-YOUR-VIRTUAL-KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "gpt-4o-mini",
       "messages": [{"role":"user","content":"Hello"}]
     }'
   ```

That's the full path. Everything below is "now I want to do X."

---

## Concepts in 60 seconds

| Concept              | What it is                                                                                                                                                     |
|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Public Model Name**| The string clients send as `"model"` (e.g. `gpt-4o-mini`, `fast-cheap`, `coding-assistant`). Yours to choose — clients never see the underlying provider.      |
| **LiteLLM Model**    | The actual provider/model string (e.g. `openai/gpt-4o-mini`, `anthropic/claude-3-5-haiku-20241022`, `bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0`).    |
| **Deployment**       | One row in the Models table — one Public Model Name + one provider + one set of credentials.                                                                   |
| **Model group**      | Two or more deployments that share the same Public Model Name. LiteLLM load-balances across them automatically.                                                |
| **Virtual Key**      | A `sk-…` key issued to a client. Has its own model allowlist, budget, expiry, team. **Never share the `LITELLM_MASTER_KEY`** — issue virtual keys instead.     |
| **Master Key**       | The single admin credential (`LITELLM_MASTER_KEY`). UI login + can do anything via API. Treat it like a root password.                                         |
| **Routing strategy** | How LiteLLM picks among deployments in a model group: round-robin (default), least-busy, lowest-cost, latency-based, usage-based.                              |
| **Fallback**         | A secondary model to try if the primary returns an error. E.g. "if `gpt-4o` is rate-limited, fall back to `claude-3-5-sonnet`."                                |

---

## 1. Configuring LLM endpoints (Models page)

The **Models** page is where you wire up providers. Each row is one
deployment. The cheat-sheet below covers the common providers.

### OpenAI (and OpenAI-compatible: Together, Groq, DeepSeek, Mistral, etc.)

| Field             | Example                                                |
|-------------------|--------------------------------------------------------|
| Provider          | `OpenAI`                                               |
| LiteLLM Model     | `openai/gpt-4o-mini`                                   |
| Public Model Name | `gpt-4o-mini`                                          |
| API Key           | `sk-proj-…`                                            |
| API Base          | leave blank for openai.com, or `https://api.together.xyz/v1` for Together, etc. |

For non-OpenAI-but-OpenAI-compatible providers, just set the API Base.
Everything else stays the same.

### Anthropic

| Field             | Example                                                |
|-------------------|--------------------------------------------------------|
| Provider          | `Anthropic`                                            |
| LiteLLM Model     | `anthropic/claude-haiku-4-5-20251001`                  |
| Public Model Name | `claude-haiku` (or whatever string clients should use) |
| API Key           | `sk-ant-…`                                             |

### Azure OpenAI

Azure is fiddlier because each deployment has its own endpoint:

| Field             | Example                                                |
|-------------------|--------------------------------------------------------|
| Provider          | `Azure OpenAI`                                         |
| LiteLLM Model     | `azure/<your-deployment-name>`                         |
| Public Model Name | `gpt-4o-azure`                                         |
| API Key           | Azure API key                                          |
| API Base          | `https://YOUR-RESOURCE.openai.azure.com`               |
| API Version       | `2024-08-01-preview` (or whatever your deployment uses)|

### AWS Bedrock

| Field             | Example                                                |
|-------------------|--------------------------------------------------------|
| Provider          | `Bedrock`                                              |
| LiteLLM Model     | `bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0`  |
| Public Model Name | `bedrock-claude`                                       |
| AWS Access Key ID | `AKIA…`                                                |
| AWS Secret Key    | `…`                                                    |
| AWS Region Name   | `us-east-1`                                            |

### Google Gemini

| Field             | Example                                                |
|-------------------|--------------------------------------------------------|
| Provider          | `Google AI Studio` (or `Vertex AI` for GCP-native)     |
| LiteLLM Model     | `gemini/gemini-2.0-flash`                              |
| Public Model Name | `gemini-flash`                                         |
| API Key           | Google AI Studio key                                   |

### Local / self-hosted (Ollama, vLLM, LM Studio)

| Field             | Example                                                |
|-------------------|--------------------------------------------------------|
| Provider          | `OpenAI` (use OpenAI-compatible mode)                  |
| LiteLLM Model     | `openai/llama3.1` (whatever model name the server reports) |
| Public Model Name | `local-llama`                                          |
| API Key           | `not-needed` (any non-empty string — the server ignores it) |
| API Base          | `http://host.docker.internal:11434/v1` (Ollama on host)|

> The `host.docker.internal` hostname only works on Docker Desktop and on
> Linux if your compose project enables it via `extra_hosts`. If you're on
> plain Linux and pointing at a service on the same host, use the host's
> LAN IP instead.

### Where these settings live

When you click **Add Model**, LiteLLM writes the row to the Postgres
`LiteLLM_ProxyModelTable`. The provider API key is **encrypted at rest**
using `LITELLM_SALT_KEY` — that's the one in `.env` that you must never
rotate. `config/config.yaml` is *not* touched.

### Editing or deleting

Click any model row → edit fields → **Save**. Deletes are immediate. There
is no soft-delete; if you want a model to be temporarily unavailable, set
its TPM/RPM limits to a low number instead.

### Health checks per model

LiteLLM background-pings each model every 5 minutes (configurable in
`config.yaml` via `litellm_settings.background_health_checks: true`). The
**Models** page shows a green/red indicator per row. Models that fail
health checks are temporarily removed from routing — they come back
automatically when they recover.

---

## 2. Virtual API keys (Virtual Keys page)

This is the single most important security practice: **clients should
authenticate with virtual keys, never with `LITELLM_MASTER_KEY`**. The
master key is for admin work only.

### Creating a virtual key

Sidebar → **Virtual Keys** → **+ Create New Key**. Useful fields:

| Field                | What it does                                                                                  |
|----------------------|-----------------------------------------------------------------------------------------------|
| **Key Alias**        | Human label (e.g. `nightly-batch-job`). Shown in logs and spend reports.                      |
| **Allowed Models**   | Which Public Model Names this key can call. Leave empty for "all models."                     |
| **Max Budget (USD)** | Hard spending cap. The key gets rejected with HTTP 401 when exceeded.                         |
| **Expiry**           | Auto-revoke after a duration (`24h`, `7d`, `30d`, or a specific date).                        |
| **RPM / TPM limits** | Requests-per-minute / tokens-per-minute throttles, enforced per-key.                          |
| **Team**             | Inherit budget and model allowlist from a team (see below).                                   |
| **Metadata**         | Free-form JSON, stored alongside the key. Useful for tagging by client app, owner, etc.       |

When you click **Create**, the full `sk-…` key is shown **once**. Copy it
immediately; the UI only ever shows the *prefix* afterwards. (The full key
is hashed in the DB — there's no way to retrieve it later. If you lose it,
delete the key and create a new one.)

### Teams (when you have more than one user)

A **Team** is a budget + allowlist scope shared by multiple virtual keys.

Use case: give your "production app" team a $200/month budget across all
of its keys, and your "experiments" team $20/month. Keys created with a
team inherit those limits.

Create via sidebar → **Teams** → **+ Create New Team**. Then when issuing
a key, set the **Team** field.

### Revoking a key

Sidebar → **Virtual Keys** → click the key → **Delete**. Effect is
immediate — any in-flight request using the key continues, but the next
request gets HTTP 401.

---

## 3. Routing: load balancing across deployments

### The basic move

Add **multiple deployments with the same Public Model Name.** That's it —
LiteLLM automatically round-robins requests across them.

**Example.** You have two OpenAI accounts (one for production, one
backup), and you want to spread load across both:

| Public Model Name | Provider | LiteLLM Model         | API Key (different per row) |
|-------------------|----------|------------------------|----------------------------|
| `chat`            | OpenAI   | `openai/gpt-4o-mini`   | `sk-proj-…aaa`             |
| `chat`            | OpenAI   | `openai/gpt-4o-mini`   | `sk-proj-…bbb`             |

Clients call `model: "chat"` and never know they're being load-balanced.
If you hit rate limits on one key, LiteLLM seamlessly uses the other.

This same pattern works across providers:

| Public Model Name | Provider     | LiteLLM Model                                |
|-------------------|--------------|----------------------------------------------|
| `smart-mid`       | OpenAI       | `openai/gpt-4o-mini`                         |
| `smart-mid`       | Anthropic    | `anthropic/claude-haiku-4-5-20251001`        |
| `smart-mid`       | Google       | `gemini/gemini-2.0-flash`                    |

Clients call `model: "smart-mid"` and get whichever provider's turn it is.

### Picking a routing strategy

The default is `simple-shuffle` — random with weights from each
deployment's RPM/TPM limits. Other options:

| Strategy                    | Picks the deployment with…                                            |
|-----------------------------|-----------------------------------------------------------------------|
| `simple-shuffle` (default)  | Random, weighted by RPM/TPM limits.                                   |
| `least-busy`                | Fewest in-flight requests right now.                                  |
| `usage-based-routing-v2`    | Most remaining TPM/RPM headroom (good for sticky-quota providers).    |
| `latency-based-routing`     | Lowest recent p95 latency.                                            |
| `lowest-cost`               | Cheapest cost-per-token. **See §5 below.**                            |

#### Setting it via UI (LiteLLM ≥ 1.50)

Sidebar → **Settings** → **Router Settings** → **Routing Strategy** dropdown.
Pick a value → **Save**. Takes effect immediately.

#### Setting it via config.yaml (always works)

Edit `config/config.yaml`:

```yaml
router_settings:
  routing_strategy: least-busy
  # Optional: how long a deployment is "cooling down" after a failure
  cooldown_time: 30
  # Optional: max retries before giving up
  num_retries: 3
```

Then `docker compose restart litellm`. UI settings persist in Postgres
and are merged with `config.yaml` on boot; the UI value wins where both
are set.

---

## 4. Routing: fallbacks

Fallbacks kick in when a deployment **errors** (rate limit, timeout,
provider 5xx, etc.) — different from load balancing, which spreads
*healthy* traffic. Use both together: load-balance among healthy
deployments, fall back to a different model entirely when nothing in the
group works.

### Setting fallbacks via UI

Sidebar → **Settings** → **Router Settings** → **Fallbacks**. Add an entry:

| Primary Model | Fallback Models                          |
|---------------|------------------------------------------|
| `gpt-4o`      | `claude-3-5-sonnet`, `gemini-pro`        |

Save. Now any request to `gpt-4o` that errors will be retried against
`claude-3-5-sonnet`, then `gemini-pro`, before the proxy gives up.

### Setting fallbacks via config.yaml

```yaml
litellm_settings:
  fallbacks:
    - gpt-4o: ["claude-3-5-sonnet", "gemini-pro"]
    - claude-haiku: ["gpt-4o-mini"]

  # Optional: also fall back on specific HTTP status codes
  default_fallbacks: ["gpt-4o-mini"]
```

Restart the `litellm` service.

### Per-request fallbacks (client-side)

Clients can also override fallbacks for a single request by passing
`fallbacks` in the request body:

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-YOUR-VIRTUAL-KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role":"user","content":"Hello"}],
    "fallbacks": ["claude-3-5-sonnet"]
  }'
```

### What counts as a "failure" worth falling back on

By default: provider 429 (rate limit), 5xx errors, timeouts, and
connection failures. **Not** 4xx errors that indicate bad client input
(those propagate to the caller — you don't want to silently route a
malformed request to a backup model).

You can tune this with `router_settings.retry_after` and
`router_settings.allowed_fails` in `config.yaml`.

---

## 5. Routing: least-cost routing

> **Want the full mental model** of how least-cost routing ties together
> with virtual keys, model access groups, and budgets/accounting? See the
> dedicated [cost-routing-guide.md](cost-routing-guide.md) — it walks
> through a complete UI setup and explains the "model group vs. model
> access group" distinction that trips most people up.

### The setup

Lowest-cost routing picks the cheapest deployment from a model group on
every request, using each deployment's per-token pricing.

**Step 1.** Create a model group with deployments at different prices.
Add multiple models in the UI sharing the same Public Model Name:

| Public Model Name | Provider  | LiteLLM Model                          |
|-------------------|-----------|----------------------------------------|
| `cheap-chat`      | OpenAI    | `openai/gpt-4o-mini`                   |
| `cheap-chat`      | Anthropic | `anthropic/claude-haiku-4-5-20251001`  |
| `cheap-chat`      | Google    | `gemini/gemini-2.0-flash`              |

**Step 2.** Set the routing strategy to `cost-based-routing`. The config
below is the conceptual shape, **but on the current build neither the UI nor
`config.yaml` actually applies it** — the Router Settings UI page can't save
and the DB overrides `config.yaml` when `store_model_in_db: true`. You must
set it in the DB. See the gotchas + exact SQL in
[cost-routing-guide.md](cost-routing-guide.md).

```yaml
# Conceptual only — DB-overridden when store_model_in_db: true (see above)
router_settings:
  routing_strategy: cost-based-routing   # docs call this "lowest-cost"
```

**Step 3.** Clients call `model: "cheap-chat"`. LiteLLM consults its
built-in cost table (`model_prices_and_context_window.json` — updated
weekly with the LiteLLM release) and picks the deployment with the lowest
expected cost for the request.

### How "cheapest" is computed

For each candidate deployment, LiteLLM estimates:

```
cost ≈ (input_tokens × input_cost_per_token) + (estimated_output_tokens × output_cost_per_token)
```

Input tokens are exact (LiteLLM tokenizes the request). Output tokens are
estimated (LiteLLM doesn't know yet how long the response will be) — by
default LiteLLM assumes a moderate output length.

### Overriding prices (for unlisted or custom-priced models)

If a model isn't in LiteLLM's built-in cost table (or you have negotiated
pricing different from list), set `input_cost_per_token` and
`output_cost_per_token` per deployment.

In the UI: edit the model row → **Advanced Settings** → **Model Info** →
set both costs (in USD per token, so $3/M tokens = 0.000003).

In `config.yaml`:

```yaml
model_list:
  - model_name: cheap-chat
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      input_cost_per_token: 0.00000015   # $0.15 per 1M input tokens
      output_cost_per_token: 0.0000006   # $0.60 per 1M output tokens
```

### Combining least-cost with quality floors

A pure least-cost router happily routes everything to the cheapest model
even when quality matters. If you need cost + quality balance, LiteLLM has
two newer routers:

- **`auto_router/adaptive_router`** — Thompson-sampling bandit that learns
  quality vs. cost weights per request type. Set up as its own model:

  ```yaml
  model_list:
    - model_name: smart-router
      litellm_params:
        model: auto_router/adaptive_router
        adaptive_router_default_model: gpt-4o-mini
        adaptive_router_config:
          available_models: ["gpt-4o", "gpt-4o-mini"]
          weights:
            quality: 0.7
            cost: 0.3
  ```

- **`auto_router/complexity_router`** — Classifies the prompt as
  SIMPLE/MEDIUM/COMPLEX/REASONING and routes each tier to a different
  model:

  ```yaml
  model_list:
    - model_name: smart-router
      litellm_params:
        model: auto_router/complexity_router
        complexity_router_config:
          tiers:
            SIMPLE: gpt-4o-mini
            MEDIUM: gpt-4o
            COMPLEX: claude-haiku-4-5-20251001
            REASONING: o1-preview
  ```

These currently require `config.yaml` (the UI doesn't yet have screens
for them). Restart `litellm` after editing.

---

## 6. Putting it together: a realistic config

A production-ish setup with three tiers, load balancing, fallbacks, and
cost-aware routing:

```yaml
# config/config.yaml
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
  store_model_in_db: true

litellm_settings:
  drop_params: true
  telemetry: false
  cache: true
  cache_params:
    type: redis
    host: os.environ/REDIS_HOST
    port: os.environ/REDIS_PORT

  # Fallback chain: if one tier is unavailable, drop down or up
  fallbacks:
    - cheap:   ["mid", "premium"]
    - mid:     ["premium", "cheap"]
    - premium: ["mid"]

router_settings:
  routing_strategy: cost-based-routing   # docs call this "lowest-cost"
  cooldown_time: 30
  num_retries: 2

# Note: model_list below is OPTIONAL — you can manage everything from the
# UI instead. This is for documentation / disaster recovery purposes.
# UI-added models override anything specified here.
```

Then add models via UI:

| Public Name | Underlying                                  |
|-------------|---------------------------------------------|
| `cheap`     | `openai/gpt-4o-mini`                        |
| `cheap`     | `anthropic/claude-haiku-4-5-20251001`       |
| `cheap`     | `gemini/gemini-2.0-flash`                   |
| `mid`       | `openai/gpt-4o`                             |
| `mid`       | `anthropic/claude-3-5-sonnet-20241022`      |
| `premium`   | `openai/o1-preview`                         |
| `premium`   | `anthropic/claude-opus-4-7`                 |

Clients now have three knobs: `cheap` / `mid` / `premium`. Each is load-
balanced across providers, falls back across tiers on error, and within
`cheap` always picks the cheapest deployment per request.

---

## 7. Inspecting and debugging

| Where                       | What you see                                                                |
|-----------------------------|------------------------------------------------------------------------------|
| Sidebar → **Usage**         | Spend per key / team / model, time-series charts.                            |
| Sidebar → **Logs**          | Per-request log: model selected, latency, token counts, status, fallbacks.   |
| `docker compose logs -f litellm` | Live proxy logs — the place to look when "model not working."           |
| `curl localhost:4000/health/readiness` | DB + cache connectivity check.                                       |
| `docker compose exec valkey valkey-cli KEYS '*'` | Cached response keys — confirms caching is working.            |

### "My request went to the wrong model"

Sidebar → **Logs** → click the request → expand. You'll see exactly which
deployment was chosen and why (e.g. "lowest-cost: picked
`anthropic/claude-haiku` ($0.0003 < $0.0008)").

### "Why isn't fallback firing?"

Two common causes:

1. The failure is a 4xx (bad input) — by design, those don't trigger
   fallback. Fix the request.
2. The fallback target itself isn't healthy. Check sidebar → **Models** —
   any model showing a red indicator is being skipped.

---

## 8. Common operations

```bash
# Restart the proxy after editing config.yaml
docker compose restart litellm

# Pull the latest LiteLLM image (e.g. weekly for cost-table updates)
docker compose pull litellm && docker compose up -d litellm

# Inspect the live model list as the proxy sees it (uses master key)
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq .

# Inspect the live router settings
curl http://localhost:4000/get/router_settings \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq .

# Dump Postgres for backup (do this before mass-editing models!)
docker compose exec postgres pg_dump -U "$POSTGRES_USER" litellm \
  | gzip > backup-$(date +%F).sql.gz
```

---

## What this guide doesn't cover (yet)

- **MCP servers** as tools — LiteLLM can expose MCP-discovered tools to
  any model. UI page: **MCP Servers**. Out of scope here; see official
  docs.
- **Guardrails** (content filtering, PII redaction) — UI page:
  **Guardrails**. Configured per-model.
- **Prompt caching** for Anthropic / OpenAI — usually auto-detected, but
  if you want to be explicit, add `cache_control` blocks in your client
  requests. Not a proxy concern.
- **OIDC / SSO login** for the UI — replaces the master-key login flow.
  Configured via `general_settings.ui_access_mode` in `config.yaml`.

When you need any of these, the official LiteLLM docs at
<https://docs.litellm.ai> are the source of truth. This guide is for the
80% case you'll hit day-to-day.
