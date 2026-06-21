# Content-aware Drift & Resync (1.23.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the hybrid drift indicator and Resync detect and converge models whose `model_info` *contents* differ (not just present/absent), and fix the LiteLLM `/model/update` bug that prevents `model_info` changes from ever persisting on an existing model.

**Architecture:** A new shared module `app/model_content.py` defines the allowlist of UI-managed `model_info` fields + normalization, consumed by both the drift comparator (read) and the convergence writer (write) so they can't diverge. `models_client.update_model` switches to LiteLLM's `PATCH /model/{id}/update` (the only endpoint that persists `model_info`). `reconcile_models` gains a `converge_content` flag so **Resync** force-converges content while **Apply** stays staged-only. The drift endpoint reports `content_drifted[]`; the badge counts it; resync converges it.

**Tech Stack:** Python 3.12 / FastAPI / asyncpg / httpx (backend); Svelte 5 runes (frontend); pytest; the project venv is `ui/.venv` (run `ui/.venv/bin/python -m pytest`, never system python — it lacks fastapi).

## Global Constraints

- Hybrid-only (`STORE_MODEL_IN_DB=true`); config-only mode → drift `{hybrid:false,in_sync:true}`, resync `422` (unchanged from 1.22.0).
- All model comparison stays in **`model_info.id`-space** (the 1.21.1 invariant). `diff_models` stays **pure and unchanged**.
- The drift badge must not raise false positives: compare **only UI-managed `model_info` fields**; never inspect litellm-derived fields (`created_at`, `updated_at`, `db_model`, encrypted keys).
- Resync stays **preview → confirm → converge**; deletions only on confirmation.
- **Apply semantics unchanged**: Apply pushes staged intent only (`converge_content=False`); Resync is the out-of-band full-convergence action (`converge_content=True`).
- No literal secrets in `config.yaml`; the convergence path never logs/returns secrets.
- The spec's `managed_patch_fields` is realized as the single function `normalized_managed` (DRY — same computation serves both comparison and explicit-PATCH emission).

---

### Task 1: `app/model_content.py` — shared UI-managed model_info rule

**Files:**
- Create: `ui/app/model_content.py`
- Test: `ui/tests/test_model_content.py`

**Interfaces:**
- Produces:
  - `MANAGED_MODEL_INFO: dict[str, {"norm": callable, "default": Any}]`
  - `normalized_managed(model_info: dict | None) -> dict` — `{field: normalized_value}` for every managed field, defaults applied for absent fields.
  - `content_diff(desired_mi: dict | None, live_mi: dict | None) -> list[str]` — sorted managed-field names whose normalized values differ; `[]` == in sync.

- [ ] **Step 1: Write the failing test**

```python
# ui/tests/test_model_content.py
from app.model_content import normalized_managed, content_diff, MANAGED_MODEL_INFO


def test_managed_allowlist_contains_disable_flag():
    assert "disable_background_health_check" in MANAGED_MODEL_INFO


def test_normalized_managed_default_and_bool():
    assert normalized_managed({}) == {"disable_background_health_check": False}
    assert normalized_managed(None) == {"disable_background_health_check": False}
    assert normalized_managed({"disable_background_health_check": None}) == {"disable_background_health_check": False}
    assert normalized_managed({"disable_background_health_check": True}) == {"disable_background_health_check": True}


def test_content_diff_detects_true_vs_absent():
    assert content_diff({"disable_background_health_check": True}, {}) == ["disable_background_health_check"]


def test_content_diff_absent_equals_false_no_drift():
    assert content_diff({}, {"disable_background_health_check": False}) == []
    assert content_diff({}, {}) == []


def test_content_diff_ignores_unmanaged_fields():
    # litellm-derived fields differ but are not managed → no drift
    assert content_diff({"id": "x", "created_at": "t1"},
                        {"id": "x", "created_at": "t2", "db_model": True}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && .venv/bin/python -m pytest tests/test_model_content.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.model_content'`

- [ ] **Step 3: Write minimal implementation**

```python
# ui/app/model_content.py
from __future__ import annotations
from typing import Any

# Single source of truth for the model_info fields the UI manages. Each field
# has a normalizer (coerce desired/live values to a comparable form) and a
# default for when the field is absent. The drift comparator (read) and the
# convergence PATCH builder (write) both use this, so they cannot disagree.
MANAGED_MODEL_INFO: dict[str, dict[str, Any]] = {
    "disable_background_health_check": {"norm": lambda v: bool(v), "default": False},
}


def normalized_managed(model_info: dict | None) -> dict:
    """{field: normalized value} for every managed field, applying defaults for
    absent fields. litellm-derived fields (created_at, db_model, …) are ignored."""
    mi = model_info or {}
    return {f: spec["norm"](mi.get(f, spec["default"])) for f, spec in MANAGED_MODEL_INFO.items()}


def content_diff(desired_mi: dict | None, live_mi: dict | None) -> list[str]:
    """Sorted managed-field names whose normalized values differ. [] == in sync."""
    d, l = normalized_managed(desired_mi), normalized_managed(live_mi)
    return sorted(f for f in MANAGED_MODEL_INFO if d[f] != l[f])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && .venv/bin/python -m pytest tests/test_model_content.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add ui/app/model_content.py ui/tests/test_model_content.py
git commit -m "feat: model_content — shared UI-managed model_info allowlist + content_diff"
```

---

### Task 2: `models_client.update_model` → `PATCH /model/{id}/update`

**Files:**
- Modify: `ui/app/models_client.py:31-35` (the `update_model` method)
- Test: `ui/tests/test_models_client.py`

**Why:** LiteLLM's old `POST /model/update` writes only `litellm_params` to the DB — it drops `model_info`. The PATCH endpoint `/model/{model_id}/update` persists `model_info`. We also overlay `normalized_managed(...)` so UI-managed fields are sent **explicitly** (e.g. `disable=false`, not omitted), making PATCH-merge overwrite in both directions (ticking AND un-ticking).

**Interfaces:**
- Consumes: `app.model_content.normalized_managed` (Task 1).
- Produces: `update_model(payload)` now issues `PATCH {base}/model/{quote(id)}/update` with body = payload whose `model_info` has managed fields made explicit.

- [ ] **Step 1: Write the failing test**

```python
# ui/tests/test_models_client.py
import json, httpx, pytest
from app.models_client import ModelsClient


def _capture():
    seen = {}
    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content.decode())
        return httpx.Response(200, json={"ok": True})
    return seen, httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_update_model_uses_patch_endpoint_with_explicit_managed_fields():
    seen, transport = _capture()
    c = ModelsClient("http://proxy:4000", "sk-master", transport=transport)
    # desired model_info OMITS the managed field (un-ticked) — PATCH must still send it explicitly as False
    await c.update_model({"model_name": "m", "litellm_params": {"model": "openai/x"},
                          "model_info": {"id": "abc-123"}})
    assert seen["method"] == "PATCH"
    assert seen["url"] == "http://proxy:4000/model/abc-123/update"
    assert seen["body"]["model_info"]["disable_background_health_check"] is False


@pytest.mark.asyncio
async def test_update_model_preserves_explicit_true():
    seen, transport = _capture()
    c = ModelsClient("http://proxy:4000", "sk-master", transport=transport)
    await c.update_model({"model_name": "m", "litellm_params": {"model": "openai/x"},
                          "model_info": {"id": "abc-123", "disable_background_health_check": True}})
    assert seen["body"]["model_info"]["disable_background_health_check"] is True


@pytest.mark.asyncio
async def test_update_model_requires_id():
    _, transport = _capture()
    c = ModelsClient("http://proxy:4000", "sk-master", transport=transport)
    with pytest.raises(ValueError):
        await c.update_model({"model_name": "m", "litellm_params": {}, "model_info": {}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && .venv/bin/python -m pytest tests/test_models_client.py -v`
Expected: FAIL — current `update_model` POSTs to `/model/update` (method/url mismatch; no explicit managed field).

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `ui/app/models_client.py` (after the existing `import httpx`):

```python
from urllib.parse import quote
from app.model_content import normalized_managed
```

Replace the `update_model` method (lines 31-35) with:

```python
    async def update_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        # LiteLLM's old POST /model/update drops model_info; the PATCH endpoint
        # /model/{id}/update persists it. Overlay normalized_managed so UI-managed
        # model_info fields are explicit → PATCH-merge overwrites both ways.
        mid = (payload.get("model_info") or {}).get("id")
        if not mid:
            raise ValueError("update_model requires model_info.id")
        body = dict(payload)
        mi = dict(payload.get("model_info") or {})
        mi.update(normalized_managed(mi))
        body["model_info"] = mi
        async with self._client() as c:
            r = await c.patch(f"{self._base}/model/{quote(str(mid), safe='')}/update", json=body)
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && .venv/bin/python -m pytest tests/test_models_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ui/app/models_client.py ui/tests/test_models_client.py
git commit -m "fix: update_model uses PATCH /model/{id}/update (old POST drops model_info)"
```

---

### Task 3: `reconcile_models(converge_content=False)`

**Files:**
- Modify: `ui/app/model_reconcile.py:59-73` (the `reconcile_models` signature + the block before `diff_models`)
- Test: `ui/tests/test_model_reconcile.py` (append)

**Interfaces:**
- Consumes: `app.model_content.content_diff` (Task 1).
- Produces: `reconcile_models(desired_items, live, client, *, changed_item_names, creds_changed, resolve_key, converge_content=False)`. When `converge_content=True`, ids whose `model_info` content differs (over `desired ∩ live`, by `model_info.id`) are unioned into the update set. `diff_models` is unchanged.

- [ ] **Step 1: Write the failing test** (append to `ui/tests/test_model_reconcile.py`)

```python
def _model_item_mi(name, model_info):
    return {"kind": "model", "name": name,
            "data": {"model_name": name, "litellm_params": {"model": "openai/x"}, "model_info": dict(model_info)},
            "flag": None}


@pytest.mark.asyncio
async def test_converge_content_updates_drifted_model_info():
    client = FakeModelsClient()
    # ui_config wants disable=true; live (id "a") has it absent → content drift
    items = [_model_item_mi("a", {"id": "a", "disable_background_health_check": True})]
    live = [{"model_name": "a", "litellm_params": {"model": "openai/x"}, "model_info": {"id": "a"}}]
    rep = await reconcile_models(items, live, client, changed_item_names=set(), creds_changed=set(),
                                 resolve_key=lambda n: None, converge_content=True)
    assert rep["updated"] == 1
    assert client.updated[0]["model_info"]["id"] == "a"


@pytest.mark.asyncio
async def test_converge_content_false_leaves_drift_untouched():
    client = FakeModelsClient()
    items = [_model_item_mi("a", {"id": "a", "disable_background_health_check": True})]
    live = [{"model_name": "a", "litellm_params": {"model": "openai/x"}, "model_info": {"id": "a"}}]
    rep = await reconcile_models(items, live, client, changed_item_names=set(), creds_changed=set(),
                                 resolve_key=lambda n: None, converge_content=False)
    assert rep["updated"] == 0 and client.updated == []


@pytest.mark.asyncio
async def test_converge_content_skips_when_content_matches():
    client = FakeModelsClient()
    items = [_model_item_mi("a", {"id": "a", "disable_background_health_check": True})]
    live = [{"model_name": "a", "litellm_params": {"model": "openai/x"},
             "model_info": {"id": "a", "disable_background_health_check": True}}]
    rep = await reconcile_models(items, live, client, changed_item_names=set(), creds_changed=set(),
                                 resolve_key=lambda n: None, converge_content=True)
    assert rep["updated"] == 0   # already converged → no update
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && .venv/bin/python -m pytest tests/test_model_reconcile.py -k converge_content -v`
Expected: FAIL — `reconcile_models() got an unexpected keyword argument 'converge_content'`

- [ ] **Step 3: Write minimal implementation**

Add the import at the top of `ui/app/model_reconcile.py` (after the existing `from app.config_render import render_model_entry`):

```python
from app.model_content import content_diff
```

Change the `reconcile_models` signature (line 59-61) to add the keyword-only flag:

```python
async def reconcile_models(desired_items, live, client,
                           changed_item_names: set[str], creds_changed: set[str],
                           resolve_key: Callable[[str], Optional[str]],
                           converge_content: bool = False) -> dict[str, Any]:
```

Then, immediately before `plan = diff_models(desired, live, changed_ids, force_ids)` (line 73), insert:

```python
    if converge_content:
        live_by_id = {(m.get("model_info") or {}).get("id"): m for m in live}
        content_ids = {mid for mid in (set(desired) & set(live_by_id))
                       if content_diff(desired[mid].get("model_info") or {},
                                       (live_by_id[mid].get("model_info") or {}))}
        changed_ids = changed_ids | content_ids
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && .venv/bin/python -m pytest tests/test_model_reconcile.py -v`
Expected: PASS (all existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add ui/app/model_reconcile.py ui/tests/test_model_reconcile.py
git commit -m "feat: reconcile_models converge_content flag (resync-only content convergence)"
```

---

### Task 4: drift `content_drifted` + resync `converge_content=True`

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py:12` (import), `:167-179` (resync route), `:181-198` (drift route)
- Test: `ui/tests/test_config_v3_routes.py` (append)

**Interfaces:**
- Consumes: `content_diff` (Task 1), `reconcile_models(..., converge_content=True)` (Task 3).
- Produces: `GET /api/config/drift` returns `content_drifted: [{id, model_name, fields:[...]}]` and folds it into `in_sync`. `POST /api/config/resync` converges content.

- [ ] **Step 1: Write the failing test** (append to `ui/tests/test_config_v3_routes.py`)

```python
def _m_mi(name, model_info):
    return {"kind": "model", "name": name,
            "data": {"model_name": name, "litellm_params": {"model": "openai/x"}, "model_info": dict(model_info)}}


def test_drift_reports_content_drift(tmp_path, monkeypatch):
    store = ModelStore([_m_mi("a", {"id": "a", "disable_background_health_check": True})])
    live = [{"model_name": "a", "model_info": {"id": "a"}}]   # present but flag absent → content drift
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["in_sync"] is False
    assert d["missing_in_litellm"] == [] and d["extra_in_litellm"] == []
    assert d["content_drifted"] == [{"id": "a", "model_name": "a",
                                     "fields": ["disable_background_health_check"]}]


def test_drift_content_in_sync_when_flag_matches(tmp_path, monkeypatch):
    store = ModelStore([_m_mi("a", {"id": "a", "disable_background_health_check": True})])
    live = [{"model_name": "a", "model_info": {"id": "a", "disable_background_health_check": True}}]
    d = _client_hybrid(tmp_path, store, live, monkeypatch).get("/api/config/drift").json()
    assert d["in_sync"] is True and d["content_drifted"] == []


def test_resync_updates_content_drift(tmp_path, monkeypatch):
    store = ModelStore([_m_mi("a", {"id": "a", "disable_background_health_check": True})])
    fake = FakeModelsClientRoutes([{"model_name": "a", "model_info": {"id": "a"}}])  # flag absent
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings as gs; gs.cache_clear()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_models_client = lambda: fake
    r = c.post("/api/config/resync").json()
    assert r["updated"] == 1 and r["added"] == 0 and r["deleted"] == 0
    assert fake.updated[0]["model_info"]["id"] == "a"
    gs.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py -k "content" -v`
Expected: FAIL — drift response has no `content_drifted` key; resync `updated` is 0.

- [ ] **Step 3: Write minimal implementation**

Update the import on line 12:

```python
from app.model_reconcile import build_desired, diff_models, reconcile_models
from app.model_content import content_diff
```

Replace the resync `reconcile_models(...)` call (lines 178-179) with the `converge_content=True` form:

```python
    return await reconcile_models(model_items, live, client,
                                  changed_item_names=set(), creds_changed=set(),
                                  resolve_key=resolve_key, converge_content=True)
```

Replace the drift route body (lines 193-198) — after `live_by_id` is built — with content-aware computation:

```python
    plan = diff_models(desired, live, set(), set())
    live_by_id = {(m.get("model_info") or {}).get("id"): m for m in live}
    missing = [{"id": e["model_info"]["id"], "model_name": e.get("model_name")} for e in plan["to_add"]]
    extra = [{"id": i, "model_name": (live_by_id.get(i) or {}).get("model_name")} for i in plan["to_delete"]]
    content = []
    for mid in sorted(set(desired) & set(live_by_id)):
        fields = content_diff(desired[mid].get("model_info") or {},
                              (live_by_id[mid].get("model_info") or {}))
        if fields:
            content.append({"id": mid, "model_name": desired[mid].get("model_name"), "fields": fields})
    return {"hybrid": True, "in_sync": not missing and not extra and not content,
            "missing_in_litellm": missing, "extra_in_litellm": extra, "content_drifted": content}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py -v`
Expected: PASS (all existing drift/resync tests + 3 new). Existing `test_drift_in_sync` still passes (it asserts only the keys it checks).

- [ ] **Step 5: Commit**

```bash
git add ui/app/routes/config_v3_routes.py ui/tests/test_config_v3_routes.py
git commit -m "feat: drift reports content_drifted; resync converges content (converge_content=True)"
```

---

### Task 5: Models screen — badge count + resync preview include content drift

**Files:**
- Modify: `ui/frontend/src/routes/Models.svelte:184` (comment), `:195-196` (preview plan), `:200` (result text), `:241` (badge count)

**Interfaces:**
- Consumes: drift response now has `content_drifted[]`; resync result now has `updated`.

- [ ] **Step 1: Update the drift state comment (line 184)**

```javascript
  let drift = $state(null)   // { hybrid, in_sync, missing_in_litellm:[], extra_in_litellm:[], content_drifted:[] } | null
```

- [ ] **Step 2: Update the resync preview to include content updates (lines 195-196)**

Replace:

```javascript
    const miss = d.missing_in_litellm || [], extra = d.extra_in_litellm || []
    const plan = `Resync to proxy:\n  + add ${miss.length}: ${miss.map(m => m.model_name).join(', ') || '—'}\n  - delete ${extra.length}: ${extra.map(m => m.model_name).join(', ') || '—'}\n\nProceed?`
```

with:

```javascript
    const miss = d.missing_in_litellm || [], extra = d.extra_in_litellm || [], upd = d.content_drifted || []
    const plan = `Resync to proxy:\n  + add ${miss.length}: ${miss.map(m => m.model_name).join(', ') || '—'}\n  ~ update ${upd.length}: ${upd.map(m => m.model_name).join(', ') || '—'}\n  - delete ${extra.length}: ${extra.map(m => m.model_name).join(', ') || '—'}\n\nProceed?`
```

- [ ] **Step 3: Update the resync result text to include `updated` (line 200)**

Replace:

```javascript
      resyncMsg = { ok: true, text: `Resynced — ${r.added} added, ${r.deleted} deleted${(r.failed && r.failed.length) ? `, ${r.failed.length} failed` : ''}.` }
```

with:

```javascript
      resyncMsg = { ok: true, text: `Resynced — ${r.added} added, ${r.updated} updated, ${r.deleted} deleted${(r.failed && r.failed.length) ? `, ${r.failed.length} failed` : ''}.` }
```

- [ ] **Step 4: Update the badge count to include content drift (line 241)**

Replace:

```svelte
        {drift.in_sync ? 'In sync ✓' : `⚠ ${(drift.missing_in_litellm.length + drift.extra_in_litellm.length)} out of sync`}
```

with:

```svelte
        {drift.in_sync ? 'In sync ✓' : `⚠ ${(drift.missing_in_litellm.length + drift.extra_in_litellm.length + (drift.content_drifted?.length || 0))} out of sync`}
```

- [ ] **Step 5: Build to verify it compiles**

Run: `cd ui/frontend && npm run build`
Expected: `✓ built` with no errors.

- [ ] **Step 6: Commit**

```bash
git add ui/frontend/src/routes/Models.svelte
git commit -m "feat(ui): drift badge + resync preview/result include content drift"
```

---

### Task 6: Integration sweep, groq repair, release

**Files:** none (verification + release). Branch: `v3.14-timezone-fixes` (already carries the TZ fixes + this spec). Rename mentally to the 1.23.0 release branch; do NOT create a new branch.

- [ ] **Step 1: Full backend suite + frontend build**

Run: `cd ui && .venv/bin/python -m pytest tests/ -q` → expect all pass (202 prior + new).
Run: `cd ui/frontend && npm run build` → expect clean.

- [ ] **Step 2: Rebuild local hybrid stack**

```bash
cd /home/kumar/workspace/litellm
docker compose up -d --build llm-proxy-ui
# wait for litellm-ui healthy
```

- [ ] **Step 3: Playwright — the original repro now persists**

Drive http://10.0.20.85:8081 → Models → Edit `gpt-4o` → tick "Disable background health check" → Save → Apply. After apply settles, query litellm: `curl -s http://10.0.20.85:4000/model/info -H "Authorization: Bearer $MK"` and assert `gpt-4o`'s `model_info.disable_background_health_check` is now `true` (was the bug: stayed `None`).

- [ ] **Step 4: Playwright — content drift badge + resync**

Inject content drift: directly PATCH litellm to clear the flag OR add a model_info-divergent state, reload Models → badge shows `⚠ N out of sync`; click "Resync to proxy" → confirm the `~ update` preview → assert badge returns to `In sync ✓` and litellm shows the flag converged.

- [ ] **Step 5: Final whole-branch review**

Dispatch the final code-reviewer (opus) with `scripts/review-package <merge-base main HEAD> HEAD`. Address Critical/Important findings.

- [ ] **Step 6: Merge + release**

```bash
git checkout main && git pull --ff-only
git merge --no-ff <branch> -m "merge: content-aware drift & resync + timezone fixes (1.23.0)"
cd ui && .venv/bin/python -m pytest tests/ -q   # verify merged result
git push origin main      # CI semantic-release cuts 1.23.0 (feat: → minor)
git pull --rebase origin main   # get the chore(release) bot commit
# bump docker-compose.yml pin to 1.23.0, commit, push
```

- [ ] **Step 7: Deploy to .75 + repair groq**

Deploy UI-only (same flow as 1.22.0: git pull --ff-only, docker compose pull/up -d, verify litellm StartedAt unchanged). Then on the .75 UI, click **Resync to proxy** — it now detects groq's `disable_background_health_check` content drift and PATCHes it. Verify via `/model/info` that the groq deployment (`f0131005`) shows `disable_background_health_check: true`, and the drift badge reads `In sync ✓`.

---

## Self-Review

**Spec coverage:** model_content (Task 1) ✓; PATCH fix + explicit managed fields (Task 2) ✓; converge_content flag, Apply staged-only / Resync converges (Task 3) ✓; drift `content_drifted` + resync convergence (Task 4) ✓; badge + preview (Task 5) ✓; TZ fixes ride along (already committed) ✓; groq repair via resync on deploy (Task 6 Step 7) ✓; out-of-scope items (litellm_params drift, force-converge-on-Apply) honored — not implemented. ✓

**Placeholder scan:** every code step has real code; no TBD/TODO/"handle errors". ✓

**Type consistency:** `normalized_managed`/`content_diff` signatures identical across Tasks 1-4; `reconcile_models(..., converge_content=False)` keyword consistent in Tasks 3-4; drift response keys (`missing_in_litellm`, `extra_in_litellm`, `content_drifted`) consistent between Task 4 (backend) and Task 5 (frontend); resync result keys (`added`, `updated`, `deleted`, `failed`) consistent between reconcile (Task 3) and frontend (Task 5). ✓
