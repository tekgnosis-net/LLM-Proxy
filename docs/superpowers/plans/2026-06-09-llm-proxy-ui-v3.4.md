# LLM-Proxy Admin UI — v3.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Backend = TDD (pytest in `ui/`, run `cd ui && .venv/bin/python -m pytest -q`). Frontend (Svelte 5) = build (`cd ui/frontend && npm run build`) + Playwright verification. Steps use `- [ ]`. **Branch: `v3.4-ui-polish`** (already created off `main`).

**Goal:** Fix the critical model-identity overwrite bug (uuid deployments) and polish the UI — provider `<select>`, descriptive modes, per-1M costs, live cache stats, configurable proxy port + dashboard URL — plus a full UI reference guide.

**Architecture:** Model items become keyed by an opaque uuid with `model_name` inside `data` (render reads `data`, not the item key), so duplicate public names coexist. Frontend screens consume the existing v3 item store; two new system endpoints (`/api/cache/stats`, `/api/proxy-info`) feed the Caching and Dashboard screens.

**Tech Stack:** FastAPI + asyncpg + `redis.asyncio` (new dep), Svelte 5 runes, pydantic-settings, docker-compose.

**Spec:** [`../specs/2026-06-09-llm-proxy-ui-v3.4-design.md`](../specs/2026-06-09-llm-proxy-ui-v3.4-design.md).

---

## File Structure
```
ui/app/config_import.py          # MODIFY: model item name=uuid, data=full entry
ui/app/config_render.py          # MODIFY: model entry from data.model_name
ui/app/config_db.py              # MODIFY: + migrate_model_identities()
ui/app/main.py                   # MODIFY: call migration in bootstrap; register system_routes
ui/app/settings.py               # MODIFY: + litellm_proxy_port/host
ui/app/routes/system_routes.py   # CREATE: GET /api/cache/stats, GET /api/proxy-info
ui/pyproject.toml                # MODIFY: + redis dep
ui/frontend/src/lib/providers.js # MODIFY: + MODE_LABELS, cost helpers
ui/frontend/src/lib/api.js       # MODIFY: + cacheStats, proxyInfo
ui/frontend/src/routes/Models.svelte    # MODIFY: uuid rows, <select>, modes, per-1M
ui/frontend/src/routes/Caching.svelte   # MODIFY: + live stats panel
ui/frontend/src/routes/Dashboard.svelte # MODIFY: + proxy endpoint card
docker-compose.yml               # MODIFY: litellm ports var; UI env (REDIS_*, LITELLM_PROXY_*)
.env.example                     # MODIFY: + LITELLM_PROXY_PORT/HOST
setup_env_helper.sh              # MODIFY: prompt port + detect LAN IP for host
docs/admin-ui-guide.md           # CREATE: per-screen field reference
```

---

## Task 1: Model identity — import + render (uuid deployments)

**Files:** Modify `ui/app/config_import.py`, `ui/app/config_render.py`. Tests: `ui/tests/test_config_import.py`, `ui/tests/test_config_render.py`, `ui/tests/test_config_roundtrip.py` (whichever hold the round-trip + model tests — grep `test_*config*`).

- [ ] **Step 1: Failing test — duplicate model_name both render (the bug's regression).** Add to the render test file:
```python
def test_two_models_same_name_both_render():
    from app.config_render import render_config
    items = [
        {"kind": "model", "name": "id-a", "data": {"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}}},
        {"kind": "model", "name": "id-b", "data": {"model_name": "gpt-4o", "litellm_params": {"model": "azure/gpt-4o"}}},
    ]
    cfg = render_config(items, decrypt=lambda v: "")
    names = [m["model_name"] for m in cfg["model_list"]]
    assert names == ["gpt-4o", "gpt-4o"]
    assert {m["litellm_params"]["model"] for m in cfg["model_list"]} == {"openai/gpt-4o", "azure/gpt-4o"}
```
- [ ] **Step 2: Run → FAIL** (`render_config` emits `model_name` from the item `name`, so both become `id-a`/`id-b`). Run: `cd ui && .venv/bin/python -m pytest tests/ -k same_name -v`.
- [ ] **Step 3: Fix `config_render.render_config`** — the `model` branch reads `model_name` from `data`, falling back to the item name (legacy safety):
```python
        elif kind == "model":
            entry = {"model_name": data.get("model_name", name)}
            entry.update({k: v for k, v in data.items() if k != "model_name"})
            model_list.append(entry)
```
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Failing test — import assigns a uuid + keeps model_name in data.** In the import test file:
```python
def test_split_model_gets_uuid_name_and_keeps_model_name():
    import uuid
    from app.config_import import split_config
    cfg = {"model_list": [{"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}}]}
    items, _ = split_config(cfg, encrypt=lambda s: s)
    m = [i for i in items if i["kind"] == "model"][0]
    uuid.UUID(m["name"])                      # name is a valid uuid (raises if not)
    assert m["data"]["model_name"] == "gpt-4o"
    assert m["data"]["litellm_params"] == {"model": "openai/gpt-4o"}
```
- [ ] **Step 6: Run → FAIL** (name is currently `gpt-4o`, and data lacks `model_name`).
- [ ] **Step 7: Fix `config_import.split_config`** — model items get a uuid name and the FULL entry as data:
```python
import uuid
# ... inside split_config, replace the model_list loop:
    for m in (cfg.get("model_list") or []):
        items.append({"kind": "model", "name": str(uuid.uuid4()), "data": dict(m)})
```
- [ ] **Step 8: Run → PASS.**
- [ ] **Step 9: Fix the existing round-trip test.** The round-trip property is now config-LEVEL: `render_config(split_config(cfg)[0] + passthrough_item)` equals `cfg` for model_list. Update any existing assertion that checks a model item's `name == model_name` (now it's a uuid; assert `data["model_name"]` instead). Run the full config suite: `cd ui && .venv/bin/python -m pytest tests/ -k "render or import or roundtrip" -v` → all PASS.
- [ ] **Step 10: Commit** `git add ui/app/config_import.py ui/app/config_render.py ui/tests && git commit -m "fix(config): model items keyed by uuid, model_name in data (duplicate public names coexist)"`

---

## Task 2: Model identity — legacy migration (idempotent, in bootstrap)

**Files:** Modify `ui/app/config_db.py` (add method), `ui/app/main.py` (call in bootstrap). Test: `ui/tests/test_config_db.py`.

- [ ] **Step 1: Failing test** (uses the same Postgres test fixture the other `ConfigStore` tests use — grep `test_config_db.py` for the `store`/dsn fixture and reuse it):
```python
async def test_migrate_rekeys_legacy_model_items(store):
    # legacy v3.3 shape: name == model_name, data has NO model_name key
    conn = await store._conn()
    await store.ensure_schema(conn)
    await conn.execute("INSERT INTO ui_config_applied(kind,name,data) VALUES('model','gpt-4o',$1)",
                       '{"litellm_params": {"model": "openai/gpt-4o"}}')
    await conn.close()
    await store.migrate_model_identities()
    applied = await store.applied()
    models = [i for i in applied if i["kind"] == "model"]
    assert len(models) == 1
    import uuid; uuid.UUID(models[0]["name"])            # rekeyed to a uuid
    assert models[0]["data"]["model_name"] == "gpt-4o"   # model_name moved into data
    # idempotent: second run is a no-op
    await store.migrate_model_identities()
    assert len([i for i in (await store.applied()) if i["kind"] == "model"]) == 1
```
- [ ] **Step 2: Run → FAIL** (`migrate_model_identities` undefined).
- [ ] **Step 3: Implement `ConfigStore.migrate_model_identities`** in `config_db.py`:
```python
    async def migrate_model_identities(self) -> int:
        """One-time: rekey legacy model items (name=model_name, data without model_name)
        to uuid names with model_name folded into data. Idempotent. Returns rows migrated."""
        import uuid as _uuid
        migrated = 0
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            for table in (APPLIED, STAGED):
                rows = await conn.fetch(f"SELECT * FROM {table} WHERE kind='model'")
                for r in rows:
                    data = json.loads(r["data"])
                    if "model_name" in data:
                        continue                       # already new-format
                    async with conn.transaction():
                        new_data = {"model_name": r["name"], **data}
                        cols = "kind,name,data,flag" if table == STAGED else "kind,name,data"
                        vals = ("model", str(_uuid.uuid4()), json.dumps(new_data)) + \
                               ((r["flag"],) if table == STAGED else ())
                        ph = ",".join(f"${i+1}" for i in range(len(vals)))
                        await conn.execute(f"INSERT INTO {table}({cols}) VALUES({ph})", *vals)
                        await conn.execute(f"DELETE FROM {table} WHERE kind='model' AND name=$1", r["name"])
                        migrated += 1
            return migrated
        finally:
            await conn.close()
```
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Call in bootstrap.** In `main.py`'s lifespan, after the config bootstrap-import/seed and before serving, call `await store.migrate_model_identities()` (find the existing `ConfigStore`/seed bootstrap block; add the call right after it). It's a no-op on fresh/new-format DBs.
- [ ] **Step 6:** Run the full backend suite → green. `cd ui && .venv/bin/python -m pytest -q`.
- [ ] **Step 7: Commit** `git add ui/app/config_db.py ui/app/main.py ui/tests/test_config_db.py && git commit -m "feat(config): idempotent migration rekeying legacy model items to uuid identities"`

---

## Task 3: Models screen — uuid-keyed rows (frontend)

**Files:** Modify `ui/frontend/src/routes/Models.svelte`. **READ it first.** No FE unit tests — build + verify in Task 9.

- [ ] **Step 1:** Model rows now key off the item `name` (uuid) and display `data.model_name`:
  - The table `{#each modelItems as m}` uses `m.data.model_name` for the displayed name and `m.name` (uuid) for the row key / delete / undo. Wherever the code reads `m.name` as the public name, switch to `m.data.model_name`; wherever it calls `store.deleteItem('model', m.name)` / `store.discard('model', m.name)`, keep using `m.name` (the uuid) — those are correct (they target the item id).
  - Health-dot lookup keys on `m.data.model_name` (health map is keyed by public model_name).
- [ ] **Step 2:** Add-model `addModel()` generates a uuid id and puts `model_name` in data:
```javascript
  async function addModel() {
    const id = crypto.randomUUID()
    const ok = await store.stageItem('model', id, {
      model_name: form.modelName,
      litellm_params: buildParams(),
      model_info: { mode: form.mode }
    })
    if (ok) resetForm()
  }
```
- [ ] **Step 3:** Remove any client-side "duplicate model name" guard — duplicates are now valid (load-balancing group). The table may show two rows with the same public name; that is correct.
- [ ] **Step 4:** Build `cd ui/frontend && npm run build` → succeeds. Commit `git add ui/frontend/src/routes/Models.svelte && git commit -m "feat(ui): Models rows keyed by uuid deployment id; duplicate public names allowed"` (or fold into Task 4's commit if building together).

---

## Task 4: Provider `<select>` + descriptive modes + per-1M costs

**Files:** Modify `ui/frontend/src/lib/providers.js`, `ui/frontend/src/routes/Models.svelte`.

- [ ] **Step 1: `providers.js`** — add a mode-label map + cost helpers:
```javascript
// Descriptive labels for litellm `mode` values (the option value stays the raw mode).
export const MODE_LABELS = {
  chat: 'Chat Completions', embedding: 'Embeddings', completion: 'Text Completion',
  image_generation: 'Image Generation', audio_transcription: 'Audio – Transcription',
  audio_speech: 'Audio – Speech', rerank: 'Rerank', moderations: 'Moderations', responses: 'Responses',
}
export const modeLabel = (m) => MODE_LABELS[m] || m
// Cost: UI shows $/1M tokens; litellm_params stores $/token.
export const perTokenToPerM = (v) => (v == null || v === '') ? '' : Number(v) * 1e6
export const perMToPerToken = (v) => (v == null || v === '') ? null : Number(v) / 1e6
```
- [ ] **Step 2: Models.svelte — provider picker.** Replace the `<input list="provider-list">` + `<datalist>` with a native `<select>` of display names (fixes the dark-mode transparency bug structurally):
```svelte
  <label>Provider
    <select bind:value={providerSlug} onchange={onProviderChange}>
      {#each providers as p}<option value={p.provider}>{p.display_name || p.provider}</option>{/each}
    </select>
  </label>
```
(Keep `providers` loading from `api.catalogProviders()` with the pinned-then-alpha order and `FALLBACK_PROVIDERS` cold-start, as today. Remove the `<datalist>` element.)
- [ ] **Step 3: Models.svelte — mode `<select>` labels.** Import `modeLabel`; in the mode dropdown options use the descriptive label:
```svelte
  <label>Mode
    <select bind:value={form.mode}>
      {#each providerModes() as m}<option value={m}>{modeLabel(m)}</option>{/each}
    </select>
    <span class="hint">The endpoint type used for the health check.</span>
  </label>
```
- [ ] **Step 4: Models.svelte — cost per 1M.** Import `perTokenToPerM`/`perMToPerToken`. Relabel the cost inputs to "Input cost ($ / 1M tokens)" / "Output cost ($ / 1M tokens)". In `buildParams()` convert per-1M → per-token:
```javascript
    if (form.input_cost !== '' && form.input_cost != null) lp.input_cost_per_token = perMToPerToken(form.input_cost)
    if (form.output_cost !== '' && form.output_cost != null) lp.output_cost_per_token = perMToPerToken(form.output_cost)
```
In `tryAutofill()` (catalog gives per-token), display per-1M: `form.input_cost = perTokenToPerM(m.input_cost_per_token)` / `form.output_cost = perTokenToPerM(m.output_cost_per_token)`. In the models table Costs column, show `In: $(perTokenToPerM(lp.input_cost_per_token)).toFixed(2) Out: … / 1M`.
- [ ] **Step 5:** Build `cd ui/frontend && npm run build` → succeeds; grep for any leftover `datalist`/`provider-list` in Models.svelte (should be none).
- [ ] **Step 6: Commit** `git add ui/frontend/src/lib/providers.js ui/frontend/src/routes/Models.svelte && git commit -m "feat(ui): provider <select> (dark-mode safe) + descriptive mode labels + cost per 1M tokens"`

---

## Task 5: Caching stats — backend endpoint

**Files:** Modify `ui/pyproject.toml`, `ui/app/settings.py` (already has `redis_host`/`redis_port`), create `ui/app/routes/system_routes.py`, modify `ui/app/main.py`, `docker-compose.yml`. Test: `ui/tests/test_system_routes.py` (new).

- [ ] **Step 1: dep** — add to `ui/pyproject.toml` dependencies: `"redis>=5"`. Reinstall: `cd ui && .venv/bin/pip install -e . -q` (or `.venv/bin/pip install "redis>=5"`).
- [ ] **Step 2: Create `ui/app/routes/system_routes.py`** (mirror the `APIRouter(prefix="/api")` + `Depends(require_session)` auth pattern used in `catalog_routes.py` — open it to copy the auth dependency import):
```python
from __future__ import annotations
import time
from fastapi import APIRouter, Depends
from app.settings import get_settings
from app.routes.auth_routes import require_session   # use the same dep other routes use; adjust import to match

router = APIRouter(prefix="/api")


@router.get("/cache/stats")
async def cache_stats(_=Depends(require_session)):
    import redis.asyncio as redis
    s = get_settings()
    host = s.redis_host or "valkey"
    port = int(s.redis_port or 6379)
    backend = f"{host}:{port}"
    r = redis.Redis(host=host, port=port, socket_connect_timeout=2, socket_timeout=2)
    try:
        t0 = time.perf_counter()
        await r.ping()
        rtt = (time.perf_counter() - t0) * 1000
        info = await r.info()
        hits = info.get("keyspace_hits", 0) or 0
        misses = info.get("keyspace_misses", 0) or 0
        total = hits + misses
        db_keys = sum(v.get("keys", 0) for k, v in info.items()
                      if isinstance(k, str) and k.startswith("db") and isinstance(v, dict))
        return {
            "connected": True, "backend": backend, "rtt_ms": round(rtt, 2), "type": "redis",
            "used_memory": info.get("used_memory"), "used_memory_human": info.get("used_memory_human"),
            "used_memory_peak_human": info.get("used_memory_peak_human"),
            "keyspace_hits": hits, "keyspace_misses": misses,
            "hit_rate": (hits / total) if total else None,
            "evicted_keys": info.get("evicted_keys", 0), "db_keys": db_keys,
            "connected_clients": info.get("connected_clients"),
            "uptime_in_seconds": info.get("uptime_in_seconds"),
        }
    except Exception as e:
        return {"connected": False, "backend": backend, "error": str(e)}
    finally:
        try: await r.aclose()
        except Exception: pass


@router.get("/proxy-info")
async def proxy_info(_=Depends(require_session)):
    s = get_settings()
    return {"proxy_port": s.litellm_proxy_port, "proxy_host": s.litellm_proxy_host or None}
```
(If the auth dependency is named differently — e.g. a `Depends(get_current_user)` — match exactly what `catalog_routes.py`/`usage_routes.py` use.)
- [ ] **Step 3: settings.py** — add the proxy fields (redis fields already exist):
```python
    litellm_proxy_port: str = "4000"   # host-facing proxy port (compose binds it)
    litellm_proxy_host: str = ""       # LAN IP/host to advertise; empty → UI uses location.hostname
```
- [ ] **Step 4: main.py** — `from app.routes import system_routes` and `app.include_router(system_routes.router)` alongside the others.
- [ ] **Step 5: docker-compose.yml — UI container env.** Add to the `llm-proxy-ui` `environment:` block: `REDIS_HOST: valkey`, `REDIS_PORT: "6379"`, `LITELLM_PROXY_PORT: ${LITELLM_PROXY_PORT:-4000}`, `LITELLM_PROXY_HOST: ${LITELLM_PROXY_HOST:-}`.
- [ ] **Step 6: Test** `ui/tests/test_system_routes.py` — patch `redis.asyncio.Redis` with a fake exposing async `ping`/`info`/`aclose`, assert `/api/cache/stats` returns `connected:true`, `hit_rate`, `db_keys`; and `/api/proxy-info` returns the configured port. Use the app's existing TestClient/auth fixture (grep `test_auth.py` / `conftest.py` for the authenticated client). Run → PASS.
- [ ] **Step 7: Commit** `git add ui/pyproject.toml ui/app/settings.py ui/app/routes/system_routes.py ui/app/main.py docker-compose.yml ui/tests/test_system_routes.py && git commit -m "feat(ui): /api/cache/stats (valkey INFO) + /api/proxy-info; redis dep; UI redis/proxy env"`

---

## Task 6: Caching stats — frontend panel

**Files:** Modify `ui/frontend/src/lib/api.js`, `ui/frontend/src/routes/Caching.svelte`.

- [ ] **Step 1: api.js** — add `cacheStats: () => req('/api/cache/stats')`.
- [ ] **Step 2: Caching.svelte** — keep the existing read-only config card; ADD a live-stats card. On mount fetch + `setInterval(fetch, 10000)`; clear on unmount; a manual Refresh button; an "updated Ns ago" stamp. Render: connection dot + RTT; used/peak memory (human); hits / misses / hit-rate %; evictions; key count; connected clients; uptime (humanize seconds → `Xd Yh`). On `connected:false` show the error + a red dot. Sketch:
```svelte
<script>
  import { onMount, onDestroy } from 'svelte'
  import { api } from '../lib/api.js'
  let { store } = $props()
  const cache = $derived(store.itemNamed('litellm_setting', 'cache')?.data)
  const cp = $derived(store.itemNamed('litellm_setting', 'cache_params')?.data || {})
  let stats = $state(null), updatedAt = $state(0); let timer
  async function refresh() { try { stats = await api.cacheStats(); updatedAt = Date.now() } catch (e) { stats = { connected:false, error:e.message } } }
  function pct(x){ return x==null ? '—' : (x*100).toFixed(0)+'%' }
  function dur(s){ if(s==null) return '—'; const d=Math.floor(s/86400),h=Math.floor(s%86400/3600); return d?`${d}d ${h}h`:`${h}h` }
  onMount(() => { refresh(); timer = setInterval(refresh, 10000) })
  onDestroy(() => clearInterval(timer))
</script>
```
(Then the markup: the existing config rows, then a stats card driven by `stats`. Style to match the existing card. Note: `Date.now()` is fine in the browser.)
- [ ] **Step 3:** Build → succeeds. Commit `git add ui/frontend/src/lib/api.js ui/frontend/src/routes/Caching.svelte && git commit -m "feat(ui): live Valkey stats panel on Caching (10s refresh)"`

---

## Task 7: Proxy port/host env + Dashboard URL card

**Files:** Modify `.env.example`, `setup_env_helper.sh`, `ui/frontend/src/lib/api.js`, `ui/frontend/src/routes/Dashboard.svelte`. (Compose ports + settings + `/api/proxy-info` were done in Task 5.)

- [ ] **Step 1: docker-compose.yml litellm ports** — change `- "4000:4000"` to `- "${LITELLM_PROXY_PORT:-4000}:4000"` (container `--port 4000` unchanged).
- [ ] **Step 2: .env.example** — append:
```bash
# Host-facing port for the LiteLLM proxy (clients call this). Default 4000.
LITELLM_PROXY_PORT=4000
# LAN IP / host clients use to reach the proxy. Leave blank to auto-detect
# (the UI uses the host you opened it on); setup_env_helper.sh fills your LAN IP.
LITELLM_PROXY_HOST=
```
- [ ] **Step 3: setup_env_helper.sh** — add LAN-IP detection + two prompts, and write the vars in the output block (match the existing `prompt`/`printf` style). Detection:
```bash
detected_ip="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || hostname -I 2>/dev/null | awk '{print $1}')"
```
Prompt `LITELLM_PROXY_PORT` (default `${LITELLM_PROXY_PORT:-4000}`) and `LITELLM_PROXY_HOST` (default `${LITELLM_PROXY_HOST:-$detected_ip}`), preserving existing values on re-run; emit `printf 'LITELLM_PROXY_PORT=%s\n' "$LITELLM_PROXY_PORT"` and the host line into the `.env` block.
- [ ] **Step 4: api.js** — add `proxyInfo: () => req('/api/proxy-info')`.
- [ ] **Step 5: Dashboard.svelte** — add a "Proxy endpoint" card. On mount also `proxy = await api.proxyInfo().catch(()=>null)`. Compute:
```javascript
  function baseUrl() {
    if (!proxy) return ''
    const host = proxy.proxy_host || location.hostname
    return `${location.protocol}//${host}:${proxy.proxy_port}`
  }
```
Render Base URL `{baseUrl()}` and OpenAI SDK `base_url` `{baseUrl()}/v1`, each with a copy button (`navigator.clipboard.writeText`), plus a hint: "Point OpenAI-compatible clients at the `/v1` URL with a virtual key."
- [ ] **Step 6:** Build → succeeds. `docker compose config -q` (with dummy `ADMIN_PASSWORD_HASH=x SESSION_SECRET=y`) parses. Commit `git add .env.example setup_env_helper.sh docker-compose.yml ui/frontend/src/lib/api.js ui/frontend/src/routes/Dashboard.svelte && git commit -m "feat(ui): configurable LITELLM_PROXY_PORT/HOST + Dashboard proxy endpoint card"`

---

## Task 8: UI reference guide

**Files:** Create `docs/admin-ui-guide.md`.

- [ ] **Step 1:** Write the guide per spec §F — one section per screen, each setting as *expects* / *does*. Cover Dashboard (incl. the Proxy endpoint card), Usage & Spend, Models (Provider, Public Model Name [note: may repeat = load-balancing group], LiteLLM Model Name / provider model id, Credential, Mode, Upstream API Base, special fields, Input/Output cost $/1M, Test connection, health, staged flags), Routing (each field + strategy meanings), Caching (read-only config + each live stat), config.yaml rendered preview, Virtual Keys, Provider Keys, Housekeeping, Settings (passthrough, catalog sync, dark mode, export), and the Apply/Discard model. Link it from `README.md` and `docs/admin-ui.md`.
- [ ] **Step 2: Commit** `git add docs/admin-ui-guide.md README.md docs/admin-ui.md && git commit -m "docs: comprehensive per-screen admin UI field reference (admin-ui-guide.md)"`

---

## Task 9: Integration verification + release

- [ ] **Step 1:** Local-build stack: `printf 'services:\n  llm-proxy-ui:\n    build: ./ui\n' > docker-compose.override.yml`; seed config (`cp config/config.yaml.example config/config.yaml`); `docker compose up -d --build --wait`; log in; catalog sync (Settings).
- [ ] **Step 2 — the bug fix (A):** Models → add **two** deployments with the **same Public Model Name** (different providers). BOTH appear (no overwrite). Apply → `config.yaml` `model_list` has two entries with that `model_name`; `/v1/models` lists it; both are in the routing group.
- [ ] **Step 3 — picker/modes/cost (B,C):** in **dark system mode**, the provider `<select>` is readable (no transparency); mode options read "Chat Completions" etc.; cost fields say "$ / 1M tokens" and a catalog auto-fill shows e.g. `2.50`; after Apply the rendered `config.yaml` shows `input_cost_per_token` = the per-token value (×1e-6).
- [ ] **Step 4 — cache stats (D):** Caching screen shows connection dot + RTT + memory + hit-rate + uptime; values refresh (watch the "updated" stamp); stop valkey → shows disconnected gracefully; restart it.
- [ ] **Step 5 — proxy URL/port (E):** Dashboard "Proxy endpoint" shows `http://<host>:4000` + `/v1`; set `LITELLM_PROXY_PORT=4100` in `.env` + `docker compose up -d` → proxy reachable on 4100 and the card shows `:4100`; set `LITELLM_PROXY_HOST` → card uses it.
- [ ] **Step 6:** Full backend suite green; capture fresh screenshots (Models with a duplicate-name group, dark-mode provider select, Caching stats, Dashboard proxy card) into `docs/images/`. Tear down; restore config; `git status` clean.
- [ ] **Step 7 — release:** merge `v3.4-ui-polish` → `main` (`--no-ff`), push → CI cuts **`1.14.0`** + image; bump the compose pin to `1.14.0` (rebase past the release commit); push. (The user tests the released image on the host `10.0.20.75:8081`.)

## Self-Review
- **Spec coverage:** A → T1+T2+T3; B+D-bug → T4 (picker/modes) ; C → T4 (cost); D → T5+T6; E → T5(settings/endpoint/compose)+T7; F → T8; verify+release → T9. ✓
- **Type consistency:** model item = `{kind:'model', name:<uuid>, data:{model_name, litellm_params, model_info}}` used in T1/T2/T3; `render_config` reads `data.model_name` (T1); `MODE_LABELS`/`modeLabel`/`perMToPerToken`/`perTokenToPerM` defined T4 used T4; `/api/cache/stats` shape (T5) consumed T6; `/api/proxy-info`={proxy_port,proxy_host} (T5) consumed T7; `api.cacheStats`/`api.proxyInfo` (T6/T7). ✓
- **Placeholders:** auth dependency name flagged to match `catalog_routes.py` exactly (can't know without reading); migration/render/endpoint code complete; FE rewiring concrete. ✓
