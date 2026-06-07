# LLM-Proxy Admin UI — v2.4 Design

Follow-up to v2 from real-world testing. Three items: a **flow-bug fix** (Provider
Keys save reported failure for a success), a **Discard staged changes** escape
hatch, and a **catalog-driven provider picker** for the Models form (provider list
+ supported modes from LiteLLM's own data).

> Builds on v2.1–v2.3. Same guardrails carry forward. Spec series:
> [v2](2026-06-07-llm-proxy-ui-v2-design.md).

## Background (what testing exposed)

A user sequence — save a Provider key (got an error) → Models > Add > Cancel →
delete the key — produced a confusing state ("unapplied changes 2 → 1, model_list
remaining", and "the key was in the list even though I got an error"). Root-caused:

- The Provider Keys **save threw `Attempted to assign to readonly property`** —
  `add()` did `store.pending = true`, but the store exposes `pending` as a
  getter-only. The throw happened *after* `createCredential` already succeeded, so
  the backend created + staged the key while the UI showed an **error** and skipped
  its list-refresh — hence "the key appeared when I came back (?!)".
- The "2 → 1" count was the **staging working correctly** (credential_list +
  model_list, minus credential_list on delete). The real gap: **no way to discard a
  staged change** — only Apply.
- Separately, the Models provider picker is **6 hardcoded presets**; the user wants
  it driven from LiteLLM's provider list with mode options per provider.

## Goals
1. Provider Keys save no longer misreports a successful create (the read-only fix).
2. A **Discard** action that reverts staged (unapplied) edits to the last-applied baseline.
3. Models **provider picker driven from the synced LiteLLM catalog** (157 providers),
   with **Mode options filtered to what each provider supports**, and sensible
   per-provider field display.

## Non-goals
- No change to the staged-Save→Apply *model* (it's correct; we add a discard, not replace it).
- **No base-URL storage or display.** LiteLLM resolves the provider endpoint itself at
  request time from the model's `provider/` prefix (see Verified facts), so the UI does
  not need, store, or show base URLs. `api_base` is offered only as an **advanced override
  for custom/self-hosted endpoints**, hidden by default.
- No `store_model_in_db` change.

## Verified facts (LiteLLM `main`)
- **URL resolution is runtime, by prefix.** `get_llm_provider()` (`litellm_core_utils/
  get_llm_provider_logic.py`) resolves each provider's `api_base` as
  `api_base or get_secret("X_API_BASE") or "<hardcoded default>"` — ~40 providers have an
  explicit URL literal there, others use a per-provider config class or the SDK's own
  default (OpenAI/Anthropic). **The upshot: a model entry needs only the correct
  `provider/` prefix; LiteLLM fills in the endpoint itself.** The UI therefore needs the
  provider *list/prefix*, not the URLs. (These literals live in resolution *logic*, not a
  published data map, which is why we don't sync them — per the user's decision.)
- `provider_endpoints_support.json` (already synced to `ui_provider_endpoints` in v2.3):
  157 providers, each `{display_name, url (docs), endpoints:{chat_completions, embeddings,
  rerank, …: bool}}`. The clean, provider-keyed source for the **list + modes**; the slug
  is the LiteLLM prefix.
- `litellm/constants.py`: `LITELLM_CHAT_PROVIDERS`/`openai_compatible_providers` (provider
  name lists) — usable to flag which providers are openai-compatible (i.e. likely to want a
  custom `api_base`). `openai_compatible_endpoints` is a flat hostname list, not provider-keyed.
- Provider slug == LiteLLM prefix (e.g. `anthropic` → `anthropic/<model>`, `bedrock` → `bedrock/<model>`).

---

## Item 1 — Provider Keys save fix (DONE, ships with v2.4)
Already implemented (commit `0a652ec`, verified in-browser): removed the illegal
`store.pending = true`; `add()` now relies on `await store.refreshPending()` (which
the store DOES expose) to update the Apply bar after a successful create. This commit
is currently unpushed; it ships as the first thing in v2.4.

---

## Item 2 — Discard staged changes

**Backend:** new `POST /api/discard` (login-gated) → `config_store.restore_baseline(config_path)`
(copy `.applied.yaml` → `config.yaml`, 0600). **No proxy restart** — the running proxy
is already on the applied baseline, so discarding just drops the on-disk staged diff.
Returns `pending_status` (will be `{pending:false}`). If `.applied.yaml` is missing
(nothing ever applied), it's a no-op that seeds the baseline; return current status.

**Frontend:** in the global Apply bar, when `pending`, show a **Discard** button next
to **Apply**. Click → confirm ("Discard N unapplied change(s)? This reverts
`config.yaml` to the last applied state.") → `store.discard()` → `POST /api/discard`
→ `refreshPending()` + `store.load()` (re-pull the reverted config so open screens
reflect it). Add `discard()` to the store + `api.discard()`.

**Verified:** `restore_baseline()` then `pending_status()` → `{pending:false}` (tested
on the real stack; no restart needed).

---

## Item 3 — Catalog-driven provider picker (Models)

Replace the 6 hardcoded `PROVIDERS` presets with a picker driven by the synced
`ui_provider_endpoints` catalog. **Because we let LiteLLM resolve endpoints from the
prefix (option 3), the provider list is the essential, correctness-critical piece**:
every selectable provider must map to a valid LiteLLM prefix.

**Backend:** `/api/catalog/providers` already returns the 157 provider rows
`{provider, display_name, docs_url, endpoints}`. Add a derived `modes` array per
provider (endpoints→modes) — in the route. Endpoint→mode map: `chat_completions→chat`,
`embeddings→embedding`, `rerank→rerank`, `image_generation→image_generation`,
`audio_transcription→audio_transcription`, `audio_speech→audio_speech`,
`moderation(s)→moderations`, `completion→completion`, `responses→responses`.
(Best-effort; unknown endpoints ignored.)

**Frontend (Models add/edit form):**
- **Provider** becomes a **searchable/typeahead dropdown of all catalog providers**
  (`api.catalogProviders()`), sorted by display_name, common ones
  (openai/anthropic/azure/bedrock/gemini/vertex_ai) pinned to the top. This list is
  essential, so it has a robust **bundled static fallback** (the canonical
  `LITELLM_CHAT_PROVIDERS` snapshot shipped with the UI) used when the catalog hasn't
  synced yet — the picker is never empty.
- On select: the model id is prefixed with `<slug>/` (read-only prefix affix on the
  Provider-model-id field). LiteLLM resolves the endpoint from that prefix at runtime.
- **Mode** dropdown is **filtered to the provider's supported modes** (from catalog
  `endpoints`); empty/unknown → full static mode list. Mode stays user-selectable.
- **`api_base` is hidden by default** (LiteLLM resolves it). An **"Advanced: custom
  endpoint"** disclosure reveals an editable `api_base` for self-hosted/override cases
  (vLLM/Ollama/proxy). No base URL is stored or shown otherwise.
- **Special deployment fields** (the few LiteLLM doesn't express as data) come from a
  small **curated map**, shown only for those providers: `azure`→{api_base, api_version},
  `bedrock`→{aws_region_name}, `vertex_ai`→{vertex_project, vertex_location}. Everything
  else → credential/api-key only (+ the advanced api_base toggle). This curated map is
  the one hand-maintained bit.
- Keep all v2.2 features: credential dropdown, custom costs, Test connection, health,
  catalog pricing auto-fill.

**What's data-driven vs manual:** the **provider list, the `provider/` prefix, and the
supported modes** are derived from LiteLLM's own data (the synced catalog). Deployment-
specific values (a self-hosted `api_base`, Azure `api_version`, Bedrock region) are
manual, shown only where needed. Base URLs for known providers are neither stored nor
shown — LiteLLM resolves them.

**Keeping the provider list current (no new mechanism):** the list comes from
`ui_provider_endpoints`, which the **existing v2.3 catalog sync already refreshes** —
the same APScheduler job pulls `provider_endpoints_support.json` from the pinned
LiteLLM `main` URL on **boot + a configurable schedule (default weekly) + manual "Sync
now"** (Settings → LiteLLM catalog). So when LiteLLM adds/renames a provider, it appears
after the next sync. The **bundled static `LITELLM_CHAT_PROVIDERS` snapshot is only the
cold-start fallback** (before the first sync, or if GitHub is unreachable); since the
sync runs on boot, the live 157-provider list takes over within moments of startup. The
snapshot is refreshed whenever we cut a new UI image (it's just a safety net, not the
source of truth). Caveat to surface in the UI: pin the catalog source to a LiteLLM
version/tag if exact provider-set reproducibility matters (currently tracks `main`).

---

## Data flow
- Discard: Apply bar → `POST /api/discard` → restore_baseline → pending clears (no restart).
- Provider picker: Models mount → `api.catalogProviders()` (cached catalog) → dropdown;
  select → derive prefix + modes + fields; Save → staged model_list (existing path).

## Error handling
- `/api/discard`: 200 with status; 500→ surfaced. If no baseline, no-op + seed.
- `catalogProviders()` empty/unsynced → static fallback provider + mode lists (form still works).

## Security
- `/api/discard` login-gated; only touches files the UI owns; no secret exposure (it
  restores the already-applied config). Carries forward 0600/redaction from v2.

## Testing
- Backend TDD: `POST /api/discard` (pending→clears; no-baseline no-op; login-gated);
  endpoint→mode derivation (pure).
- Frontend build + real-stack: Discard clears the bar without a restart; provider
  dropdown lists catalog providers; selecting filters Mode + shows the right fields;
  Save still stages model_list.
- Regression: the exact user sequence (save key → no error → bar shows credential_list;
  add model → 2; discard → 0) end-to-end in a browser.

## Risks
- Provider dropdown of 157 needs search/typeahead to stay usable (pin common ones).
- The curated per-provider field map covers the special providers; an un-curated
  provider defaults to api-key-only (acceptable; user can still set api_base via the
  compatible path). Document that the map is the one hand-maintained bit.

## Decomposition (one plan, three parts)
v2.4 is small enough for a single plan with three task groups: (A) push the read-only
fix + add Discard (backend+frontend, TDD); (B) provider picker (catalog-driven dropdown
+ mode filter + field map); (C) integration verification (the user's sequence + Discard
+ provider select). Built subagent-driven, same as v2.
