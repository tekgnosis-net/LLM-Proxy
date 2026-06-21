# Drift Indicator + Resync (1.22.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make model drift (ui_config master ↔ litellm DB) visible at a glance on the Models screen, and provide a one-click "Resync to proxy" that previews a plan, confirms, then converges litellm to the master.

**Architecture:** Two new login-gated endpoints reuse the (now-correct) `1.21.1` reconcile internals: `GET /api/config/drift` builds the desired map (`build_desired`, keyed by `model_info.id`) and runs `diff_models` read-only; `POST /api/config/resync` runs `reconcile_models` against the **applied** state (presence convergence, hot, no restart). The Models screen shows an in-sync/out-of-sync badge and a preview→confirm→converge button.

**Tech Stack:** FastAPI, asyncpg store, Svelte 5; reuses `model_reconcile.py` from 1.21.1.

## Global Constraints

- Source spec: `docs/superpowers/specs/2026-06-21-model-drift-robustness-design.md` (§3–§5, §7–§8). Implement **Pieces 2 + 3**. **Depends on `1.21.1`** (this plan must be built on a branch off `main` *after* `1.21.1` is merged — it requires `build_desired` and the id-keyed `reconcile_models`).
- **Scope = model presence drift only.** Drift compares **applied** models (the committed master) vs litellm `/model/info` by `model_info.id`. Staged-but-unapplied changes are "pending," not drift. Param-update drift is out of scope (`/model/info` masks `api_key`).
- Resync = **add missing + delete extras** (no param-updates); **no fold, no config.yaml write, no restart** (models are hot). Deletes happen only after explicit user confirmation in the UI.
- Frontend has no unit harness: verify with `cd ui/frontend && npm run build` + Playwright on **`http://10.0.20.85:8081`** (never localhost). Backend tests: `cd ui && python -m pytest <file> -v`.
- Both endpoints behave safely in config-only mode (`store_model_in_db=false`): drift → `{hybrid:false, in_sync:true}`; resync → 422.
- Commit-no-push; human merges (`feat:` → `1.22.0`).

---

## File Structure
- `ui/app/routes/config_v3_routes.py` — add `GET /api/config/drift` and `POST /api/config/resync`. Imports: `build_desired`, `diff_models`, `reconcile_models` (from `app.model_reconcile`); `_make_resolve_key` (from `app.config_engine`); existing `make_models_client`.
- `ui/frontend/src/lib/api.js` — add `drift` + `resync` methods.
- `ui/frontend/src/routes/Models.svelte` — drift badge in the header + "Resync to proxy" button with preview/confirm.
- `ui/tests/test_config_v3_routes.py` — drift + resync route tests (reuse the `_client`/`FakeStore` harness + a fake models client).

---

### Task 1: `GET /api/config/drift` endpoint

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py`
- Test: `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Produces: `GET /api/config/drift` → `{"hybrid": bool, "in_sync": bool, "missing_in_litellm": [{"id","model_name"}], "extra_in_litellm": [{"id","model_name"}]}`; `{"error":"query_failed"}` on `/model/info` failure.
- Consumes: `build_desired`, `diff_models` (from 1.21.1); `make_config_store`, `make_models_client`, `get_settings` (existing).

- [ ] **Step 1: Write failing tests**

Add to `ui/tests/test_config_v3_routes.py` (mirror the existing harness; add a fake models client + a helper that fakes `cr.make_models_client`):

```python
class FakeModelsClientRoutes:
    def __init__(self, live): self._live = live; self.added=[]; self.updated=[]; self.deleted=[]
    async def list_models(self): return self._live
    async def add_model(self, p): self.added.append(p); return {}
    async def update_model(self, p): self.updated.append(p); return {}
    async def delete_model(self, i): self.deleted.append(i); return {}

def _client_hybrid(tmp_path, store, live, monkeypatch):
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs
    gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: FakeModelsClientRoutes(live)
    return c

class ModelStore(FakeStore):
    def __init__(self, applied_models):
        self._applied = applied_models
        self._staged = []
        self.staged_calls=[]; self.cleared=None; self.folded=False

def _m(name, mid=None):
    return {"kind":"model","name":name,"data":{"model_name":name,"litellm_params":{"model":"openai/x"},"model_info":{"id":mid or name}}}

def test_drift_in_sync(tmp_path, monkeypatch):
    store = ModelStore([_m("a"), _m("b")])
    live = [{"model_name":"a","model_info":{"id":"a"}},{"model_name":"b","model_info":{"id":"b"}}]
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["hybrid"] is True and d["in_sync"] is True
    assert d["missing_in_litellm"]==[] and d["extra_in_litellm"]==[]

def test_drift_reports_missing_and_extra(tmp_path, monkeypatch):
    store = ModelStore([_m("a"), _m("b")])               # master wants a,b
    live = [{"model_name":"a","model_info":{"id":"a"}},{"model_name":"z","model_info":{"id":"z"}}]  # litellm has a,z
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["in_sync"] is False
    assert [x["id"] for x in d["missing_in_litellm"]] == ["b"]
    assert [x["id"] for x in d["extra_in_litellm"]] == ["z"]

def test_drift_config_only_is_na(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_MODEL_IN_DB", raising=False)
    from app.settings import get_settings as gs; gs.cache_clear()
    d = _client(tmp_path, ModelStore([_m("a")])).get("/api/config/drift").json()
    assert d["hybrid"] is False and d["in_sync"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -k drift -v`
Expected: FAIL — 404 (route not found).

- [ ] **Step 3: Implement the route**

In `ui/app/routes/config_v3_routes.py` add imports and the route:

```python
from app.model_reconcile import build_desired, diff_models, reconcile_models

@router.get("/config/drift", dependencies=[Depends(login_required)])
async def config_drift():
    s = get_settings()
    if not s.store_model_in_db:
        return {"hybrid": False, "in_sync": True, "missing_in_litellm": [], "extra_in_litellm": []}
    store = make_config_store()
    model_items = [it for it in await store.applied() if it["kind"] == "model"]
    desired, _, _ = build_desired(model_items, resolve_key=None)   # presence only — no key needed
    try:
        live = await make_models_client().list_models()
    except Exception as e:
        return {"error": "query_failed", "detail": str(e)}
    plan = diff_models(desired, live, set(), set())
    live_by_id = {(m.get("model_info") or {}).get("id"): m for m in live}
    missing = [{"id": e["model_info"]["id"], "model_name": e.get("model_name")} for e in plan["to_add"]]
    extra = [{"id": i, "model_name": (live_by_id.get(i) or {}).get("model_name")} for i in plan["to_delete"]]
    return {"hybrid": True, "in_sync": not missing and not extra,
            "missing_in_litellm": missing, "extra_in_litellm": extra}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -k drift -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ui/app/routes/config_v3_routes.py ui/tests/test_config_v3_routes.py
git commit -m "feat: GET /api/config/drift — model drift (ui_config vs litellm) by model_info.id"
```

---

### Task 2: `POST /api/config/resync` endpoint

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py`
- Test: `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Produces: `POST /api/config/resync` → `{"added","updated","deleted","failed":[…]}`; 422 in config-only mode.
- Consumes: `reconcile_models` (from 1.21.1), `_make_resolve_key` (from `app.config_engine`), `make_models_client`, `_fernet`, `make_config_store`.

- [ ] **Step 1: Write failing tests**

Add to `ui/tests/test_config_v3_routes.py`:

```python
def test_resync_adds_missing(tmp_path, monkeypatch):
    store = ModelStore([_m("a"), _m("b")])
    fake = FakeModelsClientRoutes([{"model_name":"a","model_info":{"id":"a"}}])  # missing b
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs; gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: fake
    r = c.post("/api/config/resync").json()
    assert r["added"] == 1 and r["deleted"] == 0
    assert fake.added[0]["model_info"]["id"] == "b"

def test_resync_deletes_extra(tmp_path, monkeypatch):
    store = ModelStore([_m("a")])
    fake = FakeModelsClientRoutes([{"model_name":"a","model_info":{"id":"a"}},
                                   {"model_name":"z","model_info":{"id":"z"}}])  # extra z
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs; gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: fake
    r = c.post("/api/config/resync").json()
    assert r["deleted"] == 1 and fake.deleted == ["z"]

def test_resync_requires_hybrid(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_MODEL_IN_DB", raising=False)
    from app.settings import get_settings as gs; gs.cache_clear()
    assert _client(tmp_path, ModelStore([_m("a")])).post("/api/config/resync").status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -k resync -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implement the route**

In `ui/app/routes/config_v3_routes.py` add:

```python
from app.config_engine import _make_resolve_key

@router.post("/config/resync", dependencies=[Depends(login_required)])
async def config_resync():
    s = get_settings()
    if not s.store_model_in_db:
        raise HTTPException(status_code=422, detail="resync requires hybrid mode (STORE_MODEL_IN_DB=true)")
    f = _fernet(); store = make_config_store()
    applied = await store.applied()
    model_items = [it for it in applied if it["kind"] == "model"]
    resolve_key = _make_resolve_key(applied, lambda b: f.decrypt(b.encode()).decode())
    client = make_models_client()
    live = await client.list_models()
    return await reconcile_models(model_items, live, client,
                                  changed_item_names=set(), creds_changed=set(), resolve_key=resolve_key)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -k resync -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ui/app/routes/config_v3_routes.py ui/tests/test_config_v3_routes.py
git commit -m "feat: POST /api/config/resync — on-demand presence convergence (hot, no restart)"
```

---

### Task 3: Frontend — drift badge + Resync button

**Files:**
- Modify: `ui/frontend/src/lib/api.js`
- Modify: `ui/frontend/src/routes/Models.svelte`

**Interfaces:**
- Consumes: `api.drift()`, `api.resync()`.
- Produces: a header badge (in-sync/out-of-sync) + a "Resync to proxy" button with preview→confirm.

- [ ] **Step 1: Add the api methods**

In `ui/frontend/src/lib/api.js`, add to the `api` object:

```js
  drift: () => req('/api/config/drift'),
  resync: () => req('/api/config/resync', { method: 'POST' }),
```

- [ ] **Step 2: Add drift state + loader + resync handler in Models.svelte**

In the `<script>` of `ui/frontend/src/routes/Models.svelte`, add state and functions (near the other `$state`/handlers):

```js
  let drift = $state(null)   // { hybrid, in_sync, missing_in_litellm:[], extra_in_litellm:[] } | null
  async function loadDrift() {
    try { drift = await api.drift() } catch (_) { drift = null }
  }
  async function resyncToProxy() {
    let d
    try { d = await api.drift() } catch (e) { store.error = e.message; return }
    if (!d.hybrid) return
    if (d.in_sync) { store.notice = 'Already in sync with the proxy.'; return }
    const miss = d.missing_in_litellm || [], extra = d.extra_in_litellm || []
    const plan = `Resync to proxy:\n  + add ${miss.length}: ${miss.map(m => m.model_name).join(', ') || '—'}\n  - delete ${extra.length}: ${extra.map(m => m.model_name).join(', ') || '—'}\n\nProceed?`
    if (!confirm(plan)) return
    try {
      const r = await api.resync()
      store.notice = `Resynced — ${r.added} added, ${r.deleted} deleted${(r.failed && r.failed.length) ? `, ${r.failed.length} failed` : ''}.`
    } catch (e) { store.error = e.message }
    await loadDrift()
  }
```

Call `loadDrift()` from the component's `onMount` (alongside the existing health/catalog loads), and after a successful Apply. (The existing `onMount(async () => {...})` already loads health + catalog — add `await loadDrift()` there.)

- [ ] **Step 3: Add the badge + button to the header markup**

In the Models header block (where `<h1>Models</h1>` and the `+ Add model` button are), add the badge + Resync button (shown only in hybrid mode):

```svelte
  {#if drift && drift.hybrid}
    <span class="drift" class:ok={drift.in_sync} class:warn={!drift.in_sync}
      title={drift.in_sync ? 'ui_config and the proxy agree' : 'ui_config and the proxy differ'}>
      {drift.in_sync ? 'In sync ✓' : `⚠ ${(drift.missing_in_litellm.length + drift.extra_in_litellm.length)} out of sync`}
    </span>
    {#if !drift.in_sync}
      <button onclick={resyncToProxy} disabled={store.applying || store.saving}>Resync to proxy</button>
    {/if}
  {/if}
```

Add styles next to the existing header styles:

```css
  .drift{font-size:12px;padding:3px 10px;border-radius:20px}
  .drift.ok{background:#e7f7ec;color:#1d7a33}
  .drift.warn{background:#fff4e5;color:#9a5b00}
```

- [ ] **Step 4: Compile gate**

Run: `cd ui/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add ui/frontend/src/lib/api.js ui/frontend/src/routes/Models.svelte
git commit -m "feat(ui): drift badge + Resync-to-proxy (preview/confirm) on Models screen"
```

---

### Task 4: Integration + release prep

**Files:** none modified — verification gate.

- [ ] **Step 1: Full backend suite + build**

Run: `cd ui && python -m pytest tests/ -q` (all pass) and `cd ui/frontend && npm run build` (clean).

- [ ] **Step 2: Playwright on the LAN IP (hybrid stack)**

On `http://10.0.20.85:8081` with a `STORE_MODEL_IN_DB=true` stack: (a) converged state → badge shows **"In sync ✓"**; (b) inject drift (delete a model directly via litellm `/model/delete`, or remove one then re-add to ui_config without applying) → reload Models → badge shows **"⚠ N out of sync"**; (c) click **Resync to proxy** → confirm the previewed plan → badge returns to "In sync ✓", `/v1/models` matches ui_config. Capture screenshots.

- [ ] **Step 3: Hand off**

Report: branch ready, suite green, build clean, Playwright verified. Human merges → semantic-release cuts `1.22.0` + image; deploy to `.75` and bump the pin.

---

## Self-Review

**Spec coverage:** §4 drift endpoint → Task 1; §4.2 badge → Task 3; §5 resync endpoint → Task 2; §5.2 preview/confirm flow → Task 3; §3 shared `diff_models`/`reconcile_models` reuse → Tasks 1/2; §8 tests → Tasks 1/2/4. Config-only safety (drift N/A, resync 422) → Tasks 1/2. ✓

**Placeholder scan:** every step has concrete code/commands; the Svelte "tests" are the build gate + Playwright (no frontend unit harness — stated in Global Constraints). No TBDs.

**Type consistency:** drift response shape (`hybrid/in_sync/missing_in_litellm/extra_in_litellm`) consistent between Task 1 (backend) and Task 3 (frontend consumer); `reconcile_models(..., changed_item_names=set(), creds_changed=set(), resolve_key=…)` matches the 1.21.1 signature; `build_desired`/`diff_models`/`_make_resolve_key` imports match their defining modules. `api.drift`/`api.resync` defined in Task 3 Step 1, used in Step 2.
