# LLM-Proxy Admin UI — v3.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Backend = TDD (`cd ui && .venv/bin/python -m pytest -q`). Frontend (Svelte 5) = build (`cd ui/frontend && npm run build`) + Playwright on a **LAN-IP origin** (`http://10.0.20.85:8081`, NOT localhost — secure-context lesson from v3.4). Steps use `- [ ]`. **Branch: `v3.5-routing-models`** (already created).

**Goal:** Make routing first-class (single-Save, per-group, per-key), add model editing with stable deployment ids, and clarify health — across the existing v3 item model.

**Architecture:** `routing_groups` and `redis_*` are just new `router_setting` items (render is free). `model_info.id = UUID` is a one-line render change. Per-key router settings pass straight through `keys_routes` → LiteLLM `/key/generate`. The frontend gets a single-Save Routing screen, a model edit flow, a routing-groups section, and a key Router-Settings section.

**Tech Stack:** FastAPI + asyncpg, Svelte 5 runes, httpx, docker-compose.

**Spec:** [`../specs/2026-06-10-llm-proxy-ui-v3.5-design.md`](../specs/2026-06-10-llm-proxy-ui-v3.5-design.md).

---

## File Structure
```
ui/app/config_render.py          # MODIFY: model_info.id = item uuid
ui/app/config_store.py           # MODIFY: validate_config routing_groups overlap guard
ui/frontend/src/routes/Routing.svelte   # MODIFY: single Save + per-group section
ui/frontend/src/routes/Models.svelte    # MODIFY: edit-in-place flow
ui/frontend/src/routes/Keys.svelte      # MODIFY: per-key Router Settings section
config/config.yaml.example       # MODIFY: router_settings.redis_host/port
ui/app/routes/models_routes.py / Models.svelte  # MODIFY: health tooltip states (#5)
```
(No backend change for #3 — `keys_routes.create_key` already forwards the full payload.)

---

## Task 1: Routing — single "Save changes"

**Files:** Modify `ui/frontend/src/routes/Routing.svelte`. No FE unit tests — build + verify in Task 6.

- [ ] **Step 1:** Remove the per-field Save/Reset buttons. Keep each field's local `$state`, the `$effect` sync, and the `●` staged dot. Add one footer row: **Save changes** + **Reset all**.
- [ ] **Step 2:** Add `saveAll()` that stages only changed fields:
```javascript
  const FIELDS = ['routing_strategy','num_retries','timeout','cooldown_time','allowed_fails','retry_after']
  async function saveAll() {
    parseErr = ''
    // fallbacks: parse first; abort all on error
    let fb
    try { fb = JSON.parse(localFallbacks) } catch { parseErr = 'Fallbacks must be valid JSON'; return }
    const locals = { routing_strategy: localStrategy, num_retries: localNumRetries, timeout: localTimeout,
                     cooldown_time: localCooldown, allowed_fails: localAllowedFails, retry_after: localRetryAfter }
    const stored = { routing_strategy: strategy, num_retries: numRetries, timeout: timeout,
                     cooldown_time: cooldown, allowed_fails: allowedFails, retry_after: retryAfter }
    for (const k of FIELDS) {
      const v = locals[k]
      if (k === 'routing_strategy') { if (v !== stored[k]) await store.stageItem('router_setting', k, v); continue }
      if (v === '' || v == null) continue            // skip cleared numerics
      if (Number(v) !== Number(stored[k])) await store.stageItem('router_setting', k, Number(v))
    }
    if (JSON.stringify(fb) !== JSON.stringify(fallbacksRaw)) await store.stageItem('router_setting', 'fallbacks', fb)
  }
  function resetAll() { localStrategy = strategy; localNumRetries = numRetries===''?'':String(numRetries)
    localTimeout = timeout===''?'':String(timeout); localCooldown = cooldown===''?'':String(cooldown)
    localAllowedFails = allowedFails===''?'':String(allowedFails); localRetryAfter = retryAfter===''?'':String(retryAfter)
    localFallbacks = JSON.stringify(fallbacksRaw, null, 2); parseErr = '' }
```
- [ ] **Step 3:** Footer: `<button class="primary" onclick={saveAll} disabled={store.saving||store.applying}>Save changes</button> <button onclick={resetAll} ...>Reset all</button>`. Move the fallbacks `parseErr` banner near the footer.
- [ ] **Step 4:** Build → succeeds. Commit `feat(ui): Routing single Save changes (stages all edited fields)`.

---

## Task 2: Model edit-in-place + `model_info.id` = UUID

**Files:** Modify `ui/app/config_render.py` (TDD), `ui/frontend/src/routes/Models.svelte`. Test: `ui/tests/test_config_render.py`.

- [ ] **Step 1: Failing test** (render injects the uuid as model_info.id):
```python
def test_model_render_sets_model_info_id_to_item_uuid():
    from app.config_render import render_config
    items = [{"kind":"model","name":"uuid-123","data":{"model_name":"gpt-4o","litellm_params":{"model":"openai/gpt-4o"},"model_info":{"mode":"chat"}}}]
    cfg = render_config(items, decrypt=lambda v:"")
    m = cfg["model_list"][0]
    assert m["model_info"]["id"] == "uuid-123"
    assert m["model_info"]["mode"] == "chat"
    assert m["litellm_params"] == {"model":"openai/gpt-4o"}
```
- [ ] **Step 2: Run → FAIL.** `cd ui && .venv/bin/python -m pytest tests/test_config_render.py -k model_info_id -v`.
- [ ] **Step 3: Fix `config_render.py` model branch:**
```python
        elif kind == "model":
            entry = {"model_name": data.get("model_name", name)}
            mi = dict(data.get("model_info") or {})
            mi.setdefault("id", name)        # the item UUID becomes LiteLLM's deployment id
            entry.update({k: v for k, v in data.items() if k not in ("model_name", "model_info")})
            entry["model_info"] = mi
            model_list.append(entry)
```
- [ ] **Step 4: Run → PASS.** Then full suite green (`pytest -q`); verify the duplicate-name + round-trip tests still pass.
- [ ] **Step 5: Commit** `feat(config): render model_info.id = item uuid (stable deployment id; lowest_cost mitigation)`.
- [ ] **Step 6: Models.svelte edit flow.** READ the file. Add `let editingId = $state(null)`. Add an **Edit** button per non-deleted row: `onclick={() => editModel(item)}`:
```javascript
  function editModel(item) {
    const d = item.data, lp = d.litellm_params || {}
    const full = lp.model || ''
    const slash = full.indexOf('/')
    providerSlug = slash > 0 ? full.slice(0, slash) : 'openai'
    form = { modelName: d.model_name, modelId: slash > 0 ? full.slice(slash+1) : full,
      api_key_env: '', api_base: lp.api_base || '', api_version: lp.api_version || '',
      aws_region_name: lp.aws_region_name || '', vertex_project: lp.vertex_project || '', vertex_location: lp.vertex_location || '',
      credential: lp.litellm_credential_name || '', mode: (d.model_info||{}).mode || 'chat',
      input_cost: lp.input_cost_per_token!=null ? perTokenToPerM(lp.input_cost_per_token) : '',
      output_cost: lp.output_cost_per_token!=null ? perTokenToPerM(lp.output_cost_per_token) : '' }
    editingId = item.name; showAdd = true; testResult = null; autofilled = false
    showAdvanced = !!(lp.api_base)
  }
```
- [ ] **Step 7:** Rename `addModel()` → `saveModel()`: `const id = editingId || uuidv4()`; `stageItem('model', id, {...})`; on success `resetForm()`. In `resetForm()` add `editingId = null`. Wire the Save button + the Add-model header button (`showAdd` toggle) to clear `editingId` when opening a fresh add. The form heading shows "Edit model" when `editingId` else "Add model".
- [ ] **Step 8:** Build → succeeds. Commit `feat(ui): edit a model in place (re-stage under same uuid)`.

---

## Task 3: Routing Groups (per-model-name strategy)

**Files:** Modify `ui/app/config_store.py` (TDD — overlap guard), `ui/frontend/src/routes/Routing.svelte`. Test: `ui/tests/test_config_store.py`.

- [ ] **Step 1: Failing test** (a model in two groups is rejected):
```python
def test_validate_rejects_overlapping_routing_groups():
    import pytest
    from app.config_store import validate_config, ConfigError
    raw = {"router_settings": {"routing_groups": [
        {"group_name":"a","models":["gpt-4o"],"routing_strategy":"latency-based-routing"},
        {"group_name":"b","models":["gpt-4o"],"routing_strategy":"cost-based-routing"}]}}
    with pytest.raises(ConfigError):
        validate_config(raw)

def test_validate_accepts_disjoint_routing_groups():
    from app.config_store import validate_config
    raw = {"router_settings": {"routing_groups": [
        {"group_name":"a","models":["gpt-4o"],"routing_strategy":"latency-based-routing"},
        {"group_name":"b","models":["claude"],"routing_strategy":"cost-based-routing"}]}}
    validate_config(raw)   # no raise
```
- [ ] **Step 2: Run → FAIL** (overlap currently allowed).
- [ ] **Step 3: Add the guard in `validate_config`** (in `config_store.py`, after the cache_params check, before `_check_no_literal_secrets`):
```python
    rs = raw.get("router_settings")
    groups = rs.get("routing_groups") if isinstance(rs, dict) else None
    if isinstance(groups, list):
        seen = set()
        for g in groups:
            for m in (g.get("models") or []) if isinstance(g, dict) else []:
                if m in seen:
                    raise ConfigError(f"model {m!r} is in more than one routing group (each model may belong to at most one)")
                seen.add(m)
```
- [ ] **Step 4: Run → PASS.** Full suite green.
- [ ] **Step 5: Commit** `feat(config): reject a model appearing in >1 routing group`.
- [ ] **Step 6: Routing.svelte per-group section.** Below the global fields, a collapsible "Per-group routing (advanced)". Read `groups = store.itemNamed('router_setting','routing_groups')?.data ?? []` into a local `$state` (deep-copied). Model-name options come from `store.itemsOfKind('model').map(m => m.data.model_name)` (dedup). Each group row: `group_name` text, `models` multi-`<select>`, `routing_strategy` `<select>` (reuse `STRATEGIES` + `modeLabel`-style — strategies are not modes; use the raw enum). Buttons: **Add group**, per-row remove, and a **Save groups** that stages the whole list: `await store.stageItem('router_setting','routing_groups', cleanedGroups)` (drop empty groups; if the result is empty, `store.deleteItem('router_setting','routing_groups')` instead). The section's staged dot = `isStaged('routing_groups')`. Client-side overlap warning before save (mirror the backend guard); the backend 422s as a backstop.
- [ ] **Step 7:** Build → succeeds. Commit `feat(ui): routing groups editor (per-model-name strategy)`.

---

## Task 4: Per-key Router Settings (Virtual Keys)

**Files:** Modify `ui/frontend/src/routes/Keys.svelte`. (Backend `keys_routes.create_key` already forwards the payload.)

- [ ] **Step 1: Confirm the `/key/generate` field** (the one documented unknown). Bring up the litellm UI on the host (`http://10.0.20.75:8080/ui`), open Create Key → Optional Settings → **Router Settings**, set a routing strategy, and submit while watching DevTools/Playwright Network for the `POST /key/generate` body. Record the exact field (candidates: `metadata.router_settings`, a top-level `router_settings`, or `key_router_settings`). **Document it in a code comment in Keys.svelte.** If unreachable, inspect `litellm` source: `KeyRequest`/`GenerateKeyRequest` in `litellm/proxy/_types.py` for a routing field. Do not proceed to Step 2 until the field is known.
- [ ] **Step 2:** Add to `form`: `router_strategy: ''` (`''` = use global) and `router_fallbacks: ''` (JSON text). Add a collapsible "Router Settings (optional)" block in the create form: a strategy `<select>` (the enum + a leading "— use global default —" option) and a fallbacks textarea.
- [ ] **Step 3:** In `create()`, build the routing object and attach it under the **confirmed field** from Step 1. Example shape (adjust the wrapper key to Step 1's finding):
```javascript
    const rs = {}
    if (form.router_strategy) rs.routing_strategy = form.router_strategy
    if (form.router_fallbacks.trim()) { try { rs.fallbacks = JSON.parse(form.router_fallbacks) } catch { err = 'Fallbacks must be valid JSON'; busy=false; return } }
    if (Object.keys(rs).length) payload[<CONFIRMED_FIELD>] = rs   // e.g. payload.metadata = {...payload.metadata, router_settings: rs}
```
- [ ] **Step 4:** Build → succeeds. Commit `feat(ui): per-key Router Settings (strategy + fallbacks) on key create`.

---

## Task 5: Health tooltip clarity + Redis routing state

**Files:** Modify `ui/frontend/src/routes/Models.svelte` (tooltip), `config/config.yaml.example` (redis items).

- [ ] **Step 1: Health tooltip.** In `healthDot(modelName)`, distinguish the three greys so grey is explained. The health map only has applied models; a model item that is staged-`new` (never applied) should read "not applied yet"; an applied model absent from the health map reads "health check pending"; present → Healthy/Unhealthy. Pass the item's flag:
```javascript
  function healthInfo(item) {
    const name = item.data.model_name, st = healthMap[name]
    if (st === true) return { color:'#34c759', title:'Healthy' }
    if (st === false) return { color:'#ff3b30', title:'Unhealthy' }
    if (item.flag === 'new') return { color:'#c7c7cc', title:'Not applied yet — apply to start health checks' }
    return { color:'#8e8e93', title:'Health check pending (background check runs every ~5 min)' }
  }
```
Use `healthInfo(item)` in the row.
- [ ] **Step 2: Redis items.** In `config/config.yaml.example`, under `router_settings:` add:
```yaml
  redis_host: os.environ/REDIS_HOST     # routing-state sharing for cost/usage/latency strategies (Valkey)
  redis_port: os.environ/REDIS_PORT
```
(The litellm container already has `REDIS_HOST`/`REDIS_PORT`. `redis_host`/`redis_port` are not secret-guarded, so `os.environ/` refs pass validation.)
- [ ] **Step 3:** Build → succeeds; `cd ui && .venv/bin/python -m pytest -q` green. Commit `feat(ui): clearer model-health states; wire router_settings redis for routing state`.

---

## Task 6: Integration verification + release

- [ ] **Step 1:** Local-build stack (`build: ./ui`); seed config; `docker compose up -d --build --wait`; catalog sync. **Use the LAN-IP origin `http://10.0.20.85:8081` in Playwright** (not localhost).
- [ ] **Step 2 — Routing (#1):** edit strategy + a numeric + fallbacks → one **Save changes** stages all three (3 staged dots, Apply bar count = 3). Reset all reverts.
- [ ] **Step 3 — Model edit + id (#2):** Edit a model → form pre-fills → change the cost → Save → **one row** updates (`changed`, not a new row). Apply → rendered `config.yaml` shows that model's `model_info.id` = its uuid; `/v1/models` works.
- [ ] **Step 4 — Routing groups (#4):** add a group (2 models, cost-based) → preview shows `router_settings.routing_groups`; put a model in two groups → blocked (client warn + backend 422 on Apply).
- [ ] **Step 5 — Per-key (#3):** create a key with Router Settings → confirm the `/key/generate` payload carries the routing field (Network) and the key is created.
- [ ] **Step 6 — Health/Redis (#5):** model health tooltips read correctly for staged vs applied; rendered config has `router_settings.redis_host/port`. On the **host** after release: apply a cost-based group with the new `model_info.id` and confirm the `lowest_cost` error is gone (or document if it persists).
- [ ] **Step 7:** Full backend suite green; screenshots into `docs/images/` (routing single-save, model edit, routing groups, key router settings); teardown; restore config; `git status` clean.
- [ ] **Step 8 — release:** merge `v3.5-routing-models` → `main` (`--no-ff`), push → CI cuts **`1.15.0`** + image; bump the compose pin to `1.15.0` (rebase past the release commit); push.

## Self-Review
- **Spec coverage:** #1→T1; #2→T2; #4(groups)→T3; #3(per-key)→T4; #5(health+redis)→T5; verify+release→T6. ✓
- **Type consistency:** `model` item `{kind,name:<uuid>,data:{model_name,litellm_params,model_info}}`; render injects `model_info.id=name` (T2); `routing_groups` is a `router_setting` item with list data (T3) rendered by the existing section logic; `editingId`/`saveModel`/`uuidv4` (T2) consistent with v3.4's `uuidv4` helper; `healthInfo(item)` (T5). ✓
- **Placeholders:** the only deferred detail (the `/key/generate` field) is T4 Step 1's explicit deliverable with a concrete capture method and a fallback (read litellm `_types.py`) — gated before T4 proceeds. ✓
