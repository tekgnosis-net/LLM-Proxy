# LLM-Proxy Admin UI — v3.5 Design: Routing depth, model editing, per-key routing, health

**Status:** design (brainstormed 2026-06-10). Builds on shipped v3.4 (`1.14.1`). Branch: `v3.5-routing-models`.

**Why:** UI-testing feedback exposed a confusing per-field Save on Routing, no model editing, and surfaced LiteLLM's full routing model (global / per-group / per-key). This round makes routing first-class, adds model editing with stable deployment ids, and addresses the health/`lowest_cost` noise.

**LiteLLM facts this design relies on** (verified):
- `routing_strategy` resolves **Key > Team > Global** ([keys_teams_router_settings](https://docs.litellm.ai/docs/proxy/keys_teams_router_settings)).
- `router_settings.routing_groups` = list of `{group_name, models[], routing_strategy, routing_strategy_args?}`; ungrouped models use the global default; each model in ≤1 group ([routing](https://docs.litellm.ai/docs/routing)).
- Cost/usage/latency strategies share state via Redis; `model_info.id` is auto-generated (params-hash) but may be set explicitly for logging/debug.

---

## 1. Routing — single "Save changes"

**Problem:** the Routing screen has 7 per-field Save buttons. Users expect one Save; clicking the wrong one (e.g. Fallbacks) silently saves the wrong (empty) field. (Real bug hit in testing.)

**Design:** replace the per-field Save/Reset with **one "Save changes"** + **one "Reset all"** at the bottom of the card.
- `Routing.svelte`: each field still has its local `$state` + the `●` staged dot. Remove per-field buttons.
- `saveAll()`: for each managed field (`routing_strategy`, `num_retries`, `timeout`, `cooldown_time`, `allowed_fails`, `retry_after`, `fallbacks`), compare the local value to the stored value; **stage only the changed ones** (`stageItem('router_setting', key, value)`). Numeric: `Number(v)`, skip empties (or delete the item if cleared — keep simple: skip empties). Fallbacks: `JSON.parse` first (show the parse error, abort the whole save if invalid). routing_strategy: stage if changed.
- `resetAll()`: re-sync every local from the store value.
- The global Apply bar applies as today.

---

## 2. Edit a model in place + stable deployment ids

**Problem:** models can't be edited (delete + re-add only). Separately, deployments lack a stable, readable `id`.

**Design:**
- **Edit (frontend, `Models.svelte`):** each row gains an **Edit** action → opens the existing add-form **pre-filled** from the model's `data`, with an `editingId` (the item UUID) set:
  - parse `litellm_params.model` = `"<slug>/<modelId>"` → `providerSlug` + `form.modelId`; set `form.modelName = data.model_name`; map `api_base`/`api_version`/`aws_region_name`/`vertex_*` from `litellm_params`; `form.credential = litellm_params.litellm_credential_name`; `form.mode = model_info.mode`; costs `perTokenToPerM(input/output_cost_per_token)`.
  - `addModel()` becomes `saveModel()`: if `editingId` set → `stageItem('model', editingId, {...})` (re-stages under the same UUID → `changed`); else `crypto`-free `uuidv4()` (existing helper) for a new one. Clear `editingId` on reset/cancel.
- **Stable id (backend, `config_render.py`):** for a `model` item, render `model_info.id = <item name (the UUID)>`:
  ```python
  elif kind == "model":
      entry = {"model_name": data.get("model_name", name)}
      mi = dict(data.get("model_info") or {})
      mi.setdefault("id", name)          # the UUID becomes LiteLLM's deployment id
      rest = {k: v for k, v in data.items() if k not in ("model_name", "model_info")}
      entry.update(rest); entry["model_info"] = mi
      model_list.append(entry)
  ```
  Readable logs ("deployment <uuid> chosen") + the likely fix for the `lowest_cost` `None`-id crash (#5).

---

## 3. Per-key Router Settings (Virtual Keys)

**Problem:** users need different routing per application (cost-based for batch, latency for interactive). LiteLLM supports this per key (Key > Team > Global).

**Design:** the **Virtual Keys → Create** form gains an optional **Router Settings** section:
- **Routing strategy** — a `<select>` of the strategy enum **plus "— use global default —"** (omit when default).
- **Fallbacks** — JSON textarea (same shape as the global Routing screen), optional.
- Passed through our `POST /api/keys` → LiteLLM `/key/generate`. **Keys are LiteLLM-managed (not our config items)** — this is a pass-through, so it touches `keys_routes.py` + `litellm_client.create_key` + `Keys.svelte`, not the config item model.
- **Open implementation detail (confirm during build):** the exact `/key/generate` field carrying per-key router settings is not in the docs. Confirm by capturing LiteLLM's own create-key request (its UI sends it) — likely `metadata` or a dedicated `key_router_settings`/`router_settings` field — then forward the same shape. The plan's first task is this 10-minute confirmation; the rest of #3 depends on it.
- Scope: **strategy + fallbacks only** this round (reliability knobs per key = future).

---

## 4. Routing Groups (per-model-name strategy)

**Problem:** one global strategy for all groups. LiteLLM supports per-group via `router_settings.routing_groups`.

**Design:**
- **Storage:** a `router_setting` item named **`routing_groups`** whose `data` is the list `[{group_name, models, routing_strategy, routing_strategy_args?}]`. **No render change needed** — `render_config` already does `router_settings[name] = data`, so `routing_groups` → `router_settings.routing_groups = [...]` for free.
- **UI (`Routing.svelte`):** a collapsible **"Per-group routing (advanced)"** section below the global fields. List existing groups; **Add group** → `group_name` (text), `models` (multi-select from the current model names in the item store), `routing_strategy` (the enum). Edit/remove. Staging the section writes the whole `routing_groups` list as one item (staged dot on the section).
- **Validation:** each model name in **≤1 group** (client-side check + a backend guard in `validate_config`/render — overlap → 422, matching LiteLLM's init-time `ValueError`). Empty groups list → omit `routing_groups` entirely.

---

## 5. Health + `lowest_cost` noise + Redis state

**Problem:** health dot is always grey; cost-based routing spams a `lowest_cost.py … model_info … id` error; routing strategies want shared Redis state.

**Design (mostly investigation + small config adds):**
- **Health (`models_routes.py` / `Models.svelte`):** confirm the background-health path (`background_health_checks: true`) populates `/api/models/health` for **applied** models with working credentials. Staged/un-applied models are grey by definition — make the dot's tooltip say **"not applied yet"** vs **"unknown / health check pending"** vs healthy/unhealthy, so grey is explained rather than mysterious. (No litellm change; clearer UX + verify the data path on the host.)
- **`lowest_cost` crash:** the `model_info.id = UUID` from #2 is the mitigation to verify on the host after applying. If it persists, it's a LiteLLM bug (its success-logging hook on health-check calls) — document it and (optionally) gate background health checks off cost-based. No risky change blind.
- **Redis state:** add two `router_setting` items rendered into `router_settings`: `redis_host = os.environ/REDIS_HOST`, `redis_port = os.environ/REDIS_PORT` (the litellm container already has these env vars; `redis_host`/`redis_port` are not secret-guarded). Seed them in the bootstrap/example so cost/usage/latency strategies share state. Harmless at `num_workers: 1`, correct if scaled.

---

## Build phasing (one branch `v3.5-routing-models`, released as `1.15.0`)
1. **Routing single-Save** (#1) — frontend only; quick win.
2. **Model edit + `model_info.id`** (#2) — render (TDD) + Models.svelte edit flow; quick win.
3. **Routing Groups** (#4) — render is free; validation (TDD) + Routing.svelte section.
4. **Per-key Router Settings** (#3) — *first* confirm the `/key/generate` field, then keys_routes + Keys.svelte.
5. **Health/Redis** (#5) — tooltip clarity + redis items; verify health + lowest_cost on the host.
6. **Integration + release** — local-build Playwright (LAN-IP origin per the v3.4 lesson); verify on host `:8081`; merge → `1.15.0`.

## Out of scope
- Per-key reliability knobs (timeout/retries/cooldown) — future.
- Team-level router settings (only key + global this round).
- Auto/adaptive/complexity routers (config-only advanced).

## Testing
- **Backend (TDD):** render sets `model_info.id` from the UUID; `routing_groups` renders into `router_settings`; overlap validation (a model in two groups → 422); redis items render as `os.environ/` refs.
- **Frontend/Integration (Playwright, LAN-IP origin):** Routing one Save stages all edited fields; edit a model → form pre-fills → re-stages under same UUID (one row, `changed`); add a routing group → renders in the config.yaml preview; create a key with Router Settings → forwarded to litellm; health tooltip states are correct; after Apply, `/v1/models` + a request show the chosen deployment id and no `lowest_cost` crash.
