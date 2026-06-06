# LiteLLM `config.yaml` schema — UI config dictionary (reference)

The authoritative parameter set the admin UI must generate and validate. The UI
generates **config-only** `config.yaml` (`store_model_in_db: false`), so a wrong
or incomplete config crashes the proxy on reload or fails silently. Phase 2's
`config_store` pydantic models + safe-apply validator implement this dictionary.

> **Version anchor:** cross-checked against BerriAI/litellm `main` @ `22186f4`
> (2026-06) — `litellm/types/router.py` (`LiteLLMParamsTypedDict`,
> `GenericLiteLLMParams`, `CredentialLiteLLMParams`, `ModelInfo`,
> `DeploymentTypedDict`, `RouterConfig`, `RoutingStrategy`, `AllowedFailsPolicy`,
> `RetryPolicy`), `litellm/router.py` (`Router.__init__`),
> `litellm/proxy/_types.py` (`ConfigGeneralSettings`), and the docs
> ["All settings"](https://docs.litellm.ai/docs/proxy/config_settings) +
> [Routing](https://docs.litellm.ai/docs/routing). **Schemas drift between
> releases — re-verify against the pinned image tag on each LiteLLM bump.**

Top-level keys: `model_list`, `litellm_settings`, `general_settings`,
`router_settings` (+ `environment_variables`, out of scope). `router_settings`
maps to `Router.__init__` kwargs; `general_settings` maps to
`ConfigGeneralSettings` (extras tolerated; the proxy also reads many keys via
`general_settings.get(...)`, so the docs page is the authoritative key list).

## `os.environ/` indirection (everywhere)

Any string value of the form `os.environ/VAR_NAME` is resolved to that env var
at load time — the canonical secret mechanism. **UI rule: every secret-flagged
field below emits `os.environ/<VAR>`, never the literal secret.**

## 1. `model_list[]` — `model_name` + `litellm_params` + `model_info`

`litellm_params` and `model_info` are `extra="allow"` → **the UI must preserve
unknown keys it didn't set on round-trip.**

### Deployment
| param | type | required | notes |
|---|---|---|---|
| `model_name` | str | **yes** | client-facing alias; entries sharing it form a load-balanced group |
| `litellm_params` | object | **yes** | see below |
| `model_info` | object | no | metadata/cost/capabilities |

### `litellm_params` — common (all providers)
| param | type | required | notes |
|---|---|---|---|
| `model` | str | **yes** | provider-prefixed: `openai/…`, `anthropic/…`, `azure/<deployment>`, `bedrock/…`, `gemini/…`, `vertex_ai/…`, `ollama/…`. Wrong prefix → wrong-provider/crash |
| `custom_llm_provider` | str | no | force provider for prefix-less openai-compatible servers (`openai`) |
| `api_key` | str | conditionally | **SECRET** → `os.environ/` |
| `api_base` | str | conditionally | required for Azure + self-hosted/openai-compatible; wrong → connect error at call time (silent) |
| `api_version` | str | Azure | e.g. `2024-02-15-preview` |
| `organization` | str | no | OpenAI org |
| `tpm` / `rpm` | int | no | routing rate inputs |
| `weight` / `order` | int | no | load-balancing weight / priority |
| `max_parallel_requests` | int | no | per-deployment cap |
| `timeout` / `stream_timeout` | float\|str | no | per-request timeouts (str → `os.environ/`) |
| `max_retries` / `num_retries` | int | no | SDK / router retries |
| `input_cost_per_token` / `output_cost_per_token` | float | no | custom pricing |
| `input_cost_per_second` / `output_cost_per_second` | float | no | time-based pricing |
| `drop_params` | bool | no | per-deployment override |
| `max_budget` / `budget_duration` | float / str | no | deployment budget |
| `tags` / `tag_regex` | list[str] | no | tag-based routing |
| `litellm_credential_name` | str | no | reference a named credential block |
| + passthrough | — | — | preserve unknown keys |

### `litellm_params` — provider-specific
| param | provider | notes |
|---|---|---|
| `aws_access_key_id` / `aws_secret_access_key` | Bedrock | **SECRET** → `os.environ/`; omit to use IAM role |
| `aws_region_name` / `aws_bedrock_runtime_endpoint` | Bedrock | e.g. `us-east-1` |
| `vertex_project` / `vertex_location` | Vertex AI | GCP project / region |
| `vertex_credentials` | Vertex AI | **SECRET** — SA JSON or path → `os.environ/` |
| (Gemini AI Studio) | Gemini | `model: gemini/…` + `api_key` (`GEMINI_API_KEY`); no vertex_* |
| (Anthropic) | Anthropic | `model: anthropic/…` + `api_key`; optional `api_base` |
| (Ollama/vLLM/self-hosted) | local | `model: ollama/<n>` or `openai/<n>` + `custom_llm_provider: openai` + `api_base`; dummy/no `api_key` |
| `region_name` | unified | generic region (Bedrock/Vertex) |

### `model_info`
| param | type | notes |
|---|---|---|
| `id` | str | auto-UUID if omitted |
| `mode` | str | `chat`/`embedding`/`completion`/`image_generation`/`audio_transcription`/`rerank`/`moderations` — wrong → health/routing misbehave (silent) |
| `base_model` | str | Azure: real model for correct cost tracking |
| `input_cost_per_token` / `output_cost_per_token` | float | pricing (also accepted in litellm_params) |
| `input_cost_per_character`/`output_cost_per_character` | float | Vertex char pricing |
| `cache_read_input_token_cost`/`cache_creation_input_token_cost` | float | prompt-cache pricing |
| `max_tokens` | int | advertised max |
| `supports_vision`/`supports_function_calling`/`supports_reasoning`/… | bool | capability flags |
| + passthrough | — | preserve unknown keys |

## 2. `router_settings`

Valid **`routing_strategy`** (EXACT — anything else crashes Router init on load):
`simple-shuffle` (default), `least-busy`, `usage-based-routing`,
`usage-based-routing-v2`, `latency-based-routing`, `cost-based-routing`
(`provider-budget-routing` also exists in the enum). **`lowest-cost` is NOT
valid.** UI restricts to a dropdown of these exact strings.

| param | type | notes |
|---|---|---|
| `routing_strategy` | enum↑ | default `simple-shuffle`; invalid → **crash on load** |
| `routing_strategy_args` | dict | e.g. latency `ttl`, `lowest_latency_buffer` |
| `num_retries` | int | default 3 |
| `timeout` / `stream_timeout` | float | request / streaming timeout |
| `retry_after` | int | min seconds before retry |
| `retry_policy` | object | per-exception retry counts (BadRequest/Authentication/Timeout/RateLimit/ContentPolicyViolation/InternalServerError…Retries) |
| `model_group_retry_policy` | dict | per-group retry overrides |
| `cooldown_time` | float | cooldown after `allowed_fails` |
| `disable_cooldowns` | bool | |
| `allowed_fails` | int | failures/min before cooldown |
| `allowed_fails_policy` | object | per-exception allowed fails |
| `fallbacks` | list[dict] | `[{"gpt-4": ["gpt-4o"]}]` |
| `default_fallbacks` | list[str] | global fallbacks |
| `context_window_fallbacks` / `content_policy_fallbacks` | list[dict] | conditional fallbacks |
| `enable_pre_call_checks` | bool | context-window/param checks before routing |
| `model_group_alias` | dict | alias group → real group(s) |
| `routing_groups` | list | each: `group_name`, `models[]`, `routing_strategy`, `routing_strategy_args` (per-group strategy) |
| `redis_host` / `redis_port` / `redis_password` / `redis_url` / `redis_db` | — | router shared state; `redis_password`/`redis_url` **SECRET** |
| `cache_responses` / `cache_kwargs` / `caching_groups` | — | router-level response cache |
| `default_max_parallel_requests` / `default_litellm_params` | int/dict | defaults |
| + passthrough | — | preserve unknown keys |

## 3. `litellm_settings` + `cache_params`

### `litellm_settings`
| param | type | notes |
|---|---|---|
| `cache` | bool | master cache switch |
| `cache_params` | object | see below |
| `drop_params` | bool | drop unsupported params globally; off → silent provider 400s |
| `request_timeout` / `num_retries` | int | global |
| `max_budget` / `budget_duration` | float/str | global spend cap |
| `success_callback` / `failure_callback` / `callbacks` / `service_callbacks` | list[str] | loggers/observability (langfuse, datadog, prometheus…) |
| `json_logs` / `turn_off_message_logging` / `redact_user_api_key_info` | bool | logging controls |
| `telemetry` | bool | set false to disable |
| `default_fallbacks` / `context_window_fallbacks` / `content_policy_fallbacks` | list | (also valid here) |
| + passthrough | — | preserve unknown keys |

### `cache_params`
| param | type | notes |
|---|---|---|
| `type` | str | `redis`/`local`/`s3`/`gcs`/`qdrant`; unknown → cache init fail (**crash**) |
| `host` / `port` | str/int | Redis (host → `os.environ/`; port default 6379) |
| `password` | str | Redis **SECRET** → `os.environ/` |
| `namespace` / `ttl` / `mode` | str/int/str | `ttl` default 600; `mode` `default_on`/`default_off` |
| `supported_call_types` | list[str] | e.g. `["completion","acompletion","embedding"]` |
| `max_connections` | int | redis pool |
| `redis_startup_nodes` / `service_name` / `sentinel_nodes` | — | redis cluster/sentinel |
| `similarity_threshold` / `qdrant_*` | — | semantic cache |
| `s3_*` / `gcs_*` | — | s3/gcs cache (`s3_aws_secret_access_key` **SECRET**) |
| **FORBIDDEN** | — | ⛔ `ssl`, `ssl_check_hostname` (also avoid `ssl_cert_reqs`) — bug #10949; UI must never emit and the validator must reject |

## 4. `general_settings`

(`ConfigGeneralSettings` + `.get()` reads — docs page is authoritative key list; extras tolerated)

| param | type | notes |
|---|---|---|
| `master_key` | str | **SECRET** → `os.environ/LITELLM_MASTER_KEY` |
| `database_url` | str | **SECRET** → `os.environ/DATABASE_URL` (keys/spend; not models in config-only) |
| `store_model_in_db` | bool | **our stack: `false`** |
| `disable_spend_logs` / `disable_spend_updates` | bool | spend-log writes |
| `maximum_spend_logs_retention_period` | str | e.g. `30d`; invalid duration → warning, cleanup silently skipped |
| `maximum_spend_logs_retention_interval` | str | cleanup cadence (default `1d`) |
| `maximum_spend_logs_cleanup_cron` | str | cron alternative |
| `max_parallel_requests` / `global_max_parallel_requests` | int | per-key / whole-proxy caps |
| `max_request_size_mb` / `max_response_size_mb` | int | size limits |
| `alerting` / `alert_types` / `alerting_threshold` / `alert_to_webhook_url` / `alerting_args` | list/dict | alerting (webhook URLs **SECRET-adjacent**) |
| `allow_requests_on_db_unavailable` | bool | serve when DB down |
| `background_health_checks` / `health_check_interval` / `health_check_details` | bool/int | health |
| `allowed_routes` / `allowed_ips` | list | access restriction |
| `ui_access_mode` | enum | `admin_only`/`all` |
| `proxy_batch_write_at` | int | batch spend writes (default 10s) |
| + passthrough | — | preserve unknown keys |

## Secrets (emit `os.environ/<VAR>`, never literals)
`litellm_params.api_key`, `aws_secret_access_key` (+ `aws_access_key_id`),
`vertex_credentials`; `cache_params.password`, `s3_aws_secret_access_key`;
`router_settings.redis_password` / `redis_url`; `general_settings.master_key` /
`database_url`; webhook URLs in `alert_to_webhook_url`.

## ⛔ Forbidden — UI must never emit (bug #10949)
Under `cache_params`: `ssl`, `ssl_check_hostname` (and avoid `ssl_cert_reqs`).
The generator omits them; the validator rejects them if pasted in.

## Crash-on-load vs silent-misbehave (informs validation severity)
**Crashes proxy on load/reload** (validator must HARD-reject):
- invalid `routing_strategy`
- unknown `cache_params.type`, or `cache: true` with missing required redis params
- malformed YAML / wrong nesting (e.g. `litellm_params` not under a `model_list` entry)
- unrecognized `model:` provider prefix (may fail at load or first call)

**Silently misbehaves** (validator should WARN; verify post-apply via `/v1/models` + health):
- wrong `model_info.mode`; wrong `api_base`/`aws_region_name`/`vertex_location` (fail only at request time); wrong cost-per-token (wrong spend); `drop_params:false` vs a strict provider (silent 400s); invalid retention duration (cleanup silently skipped)

## How the UI uses this
- **Generation:** typed models mirror these params; secrets emitted as `os.environ/`; unknown keys preserved on round-trip.
- **Validation (safe-apply):** parse YAML → validate structure against this schema (hard-reject crash-class issues incl. the forbidden ssl keys + invalid routing_strategy) → LiteLLM-fidelity check → atomic write+backup → SIGHUP → verify health + `/v1/models` → auto-rollback. See the spec's "Config generation & safe-apply" section.
