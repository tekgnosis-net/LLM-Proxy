# Reconcile Identity Patch (1.21.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the hybrid reconciler's identity handling so models converge correctly regardless of `name`-vs-`model_info.id` divergence, credential rotation actually re-pushes affected models, and an already-present model on `/model/new` converges instead of hard-failing.

**Architecture:** All changes are in `ui/app/model_reconcile.py` (+ a keyword rename echoed at the `apply_config` call site). `diff_models` stays pure and unchanged — we only fix what's fed into it: key the desired map by the rendered entry's `model_info.id`, translate the staged signals (changed-model item-names, rotated-credential names) into that id-space, and make the `to_add` loop idempotent on a unique-constraint error.

**Tech Stack:** Python 3.12, pytest (`pytest-asyncio`), httpx. Tests are pure/fakes — no live stack.

## Global Constraints

- Source spec: `docs/superpowers/specs/2026-06-21-model-drift-robustness-design.md` (§2). Implement **Piece 1 only** (the engine patch). Drift/Resync are the separate `1.22.0` plan.
- `diff_models(desired, live, changed_ids, force_ids)` stays **pure and unchanged**; its existing tests must stay green untouched.
- The desired map is keyed by **`model_info.id`** (the rendered entry's id, which `render_model_entry` defaults to the item `name` but honors an explicit `data.model_info.id`).
- Backward-compat: `apply_config`'s positional call to `reconcile_models` keeps the same argument *order*; only param *names* change (`changed_ids`→`changed_item_names`, `force_ids`→`creds_changed`).
- Run backend tests with `cd ui && python -m pytest <file> -v` (use `.venv/bin/python -m pytest` if plain `python` lacks deps). Full suite must stay green.
- Commit-no-push; the human merges to `main` (semantic-release cuts `1.21.1` from the `fix:` commits) and deploys.

---

## File Structure
- `ui/app/model_reconcile.py` — add `build_desired()` helper; rewrite `reconcile_models` to key by `model_info.id`, translate signals, and idempotent-add. `diff_models` untouched.
- `ui/app/config_engine.py:131` — update the `reconcile_models(...)` call to the renamed keyword params (order unchanged).
- `ui/tests/test_model_reconcile.py` — update existing `reconcile_models` call sites to the new param names; add new tests for the three fixes.

---

### Task 1: Key desired by `model_info.id` + translate staged signals

**Files:**
- Modify: `ui/app/model_reconcile.py`
- Modify: `ui/app/config_engine.py:131`
- Test: `ui/tests/test_model_reconcile.py`

**Interfaces:**
- Produces: `build_desired(items, resolve_key=None) -> tuple[dict[str,dict], dict[str,str], list[dict]]` returning `(desired_by_id, name_to_id, failed)`.
- Produces: `reconcile_models(desired_items, live, client, changed_item_names: set[str], creds_changed: set[str], resolve_key) -> dict` (renamed 4th/5th params; desired keyed by `model_info.id`; `force_ids` derived from models referencing a rotated credential).
- Consumes: `render_model_entry` (existing), `diff_models` (existing, unchanged).

- [ ] **Step 1: Write failing tests for the identity fix**

Add to `ui/tests/test_model_reconcile.py`:

```python
from app.model_reconcile import build_desired


def _item_explicit_id(name, model_info_id, cred=None):
    lp = {"model": "openai/gpt-4o"}
    if cred: lp["litellm_credential_name"] = cred
    return {"kind": "model", "name": name,
            "data": {"model_name": name, "litellm_params": lp, "model_info": {"id": model_info_id}}, "flag": None}


@pytest.mark.asyncio
async def test_reconcile_keys_by_model_info_id_not_item_name():
    # item key 'fff' but model_info.id 'aaa'; litellm already has 'aaa' live → must be a NO-OP, not a re-add
    client = FakeModelsClient()
    items = [_item_explicit_id("fff", "aaa")]
    live = [{"model_name": "fff", "model_info": {"id": "aaa"}}]
    rep = await reconcile_models(items, live=live, client=client,
                                 changed_item_names=set(), creds_changed=set(), resolve_key=lambda n: "sk")
    assert client.added == [] and client.deleted == []     # 'aaa' present → nothing to do
    assert rep == {"added": 0, "updated": 0, "deleted": 0, "failed": []}


@pytest.mark.asyncio
async def test_reconcile_credential_rotation_force_updates_referencing_models():
    # model references credential 'Groq'; Groq rotated → model must be force-updated (re-pushed)
    client = FakeModelsClient()
    items = [_model_item("m1", cred="Groq")]
    live = [{"model_name": "m1", "model_info": {"id": "m1"}}]   # already live
    rep = await reconcile_models(items, live=live, client=client,
                                 changed_item_names=set(), creds_changed={"Groq"}, resolve_key=lambda n: "sk-NEW")
    assert len(client.updated) == 1 and rep["updated"] == 1     # force-update fired
    assert client.updated[0]["litellm_params"]["api_key"] == "sk-NEW"


def test_build_desired_keys_by_id_and_maps_names():
    desired, name_to_id, failed = build_desired([_item_explicit_id("fff", "aaa")], resolve_key=None)
    assert set(desired) == {"aaa"} and name_to_id == {"fff": "aaa"} and failed == []
```

- [ ] **Step 2: Update the existing reconcile call sites to the new param names**

In `ui/tests/test_model_reconcile.py`, the three existing `reconcile_models(...)` calls use `changed_ids=set(), force_ids=set()`. Change each to `changed_item_names=set(), creds_changed=set()`:

```python
# test_reconcile_adds_and_inlines_key, test_reconcile_missing_credential_reported_not_pushed,
# test_reconcile_deletes_drifted_live_model — replace:
#   changed_ids=set(), force_ids=set()
# with:
        changed_item_names=set(), creds_changed=set(),
```

(The `diff_models` tests keep `changed_ids=`/`force_ids=` — `diff_models` is unchanged.)

- [ ] **Step 3: Run the new + updated tests to verify they fail**

Run: `cd ui && python -m pytest tests/test_model_reconcile.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_desired'` (and the new tests error on the unknown `changed_item_names` kwarg).

- [ ] **Step 4: Implement `build_desired` + rewrite `reconcile_models`**

In `ui/app/model_reconcile.py`, replace the `reconcile_models` function and add `build_desired` (keep `_live_ids` and `diff_models` exactly as they are):

```python
def build_desired(items, resolve_key=None):
    """Return (desired_by_id, name_to_id, failed). desired is keyed by the rendered
    entry's model_info.id (defaults to item name, honors an explicit data.model_info.id).
    A credential that fails to resolve becomes a 'failed' entry (item skipped)."""
    desired: dict[str, dict] = {}
    name_to_id: dict[str, str] = {}
    failed: list[dict] = []
    for it in items:
        try:
            entry = render_model_entry(it, resolve_key)
        except KeyError as e:
            failed.append({"id": it["name"], "op": "resolve", "error": str(e)})
            continue
        mid = entry["model_info"]["id"]
        desired[mid] = entry
        name_to_id[it["name"]] = mid
    return desired, name_to_id, failed


def _is_already_exists(e) -> bool:
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = resp.text or ""
        except Exception:
            body = ""
    s = (str(e) + " " + body).lower()
    return ("unique constraint" in s or "already exists" in s
            or "failed to add model to db" in s)


async def reconcile_models(desired_items, live, client,
                           changed_item_names: set[str], creds_changed: set[str],
                           resolve_key: Callable[[str], Optional[str]]) -> dict[str, Any]:
    desired, name_to_id, failed = build_desired(desired_items, resolve_key)
    # Translate staged signals (item-names, credential-names) into model_info.id space.
    changed_ids = {name_to_id[n] for n in changed_item_names if n in name_to_id}
    force_ids = {
        name_to_id[it["name"]]
        for it in desired_items
        if it["name"] in name_to_id
        and (it["data"].get("litellm_params") or {}).get("litellm_credential_name") in creds_changed
    }
    plan = diff_models(desired, live, changed_ids, force_ids)
    added = updated = deleted = 0
    for entry in plan["to_add"]:
        try:
            await client.add_model(entry); added += 1
        except Exception as e:
            failed.append({"id": entry["model_info"]["id"], "op": "add", "error": str(e)})
    for entry in plan["to_update"]:
        try:
            await client.update_model(entry); updated += 1
        except Exception as e:
            failed.append({"id": entry["model_info"]["id"], "op": "update", "error": str(e)})
    for mid in plan["to_delete"]:
        try:
            await client.delete_model(mid); deleted += 1
        except Exception as e:
            failed.append({"id": mid, "op": "delete", "error": str(e)})
    return {"added": added, "updated": updated, "deleted": deleted, "failed": failed}
```

(`_is_already_exists` is defined now but only used in Task 2 — define it here to avoid a second edit of the same region; it is harmless until wired in.)

- [ ] **Step 5: Update the `apply_config` call site to the renamed params**

In `ui/app/config_engine.py` (the `reconcile_models(...)` call, ~line 131), pass the renamed keywords (argument order is unchanged):

```python
    model_report = await reconcile_models(desired_items, live, models_client,
                                          changed_item_names=changed_ids, creds_changed=creds_changed,
                                          resolve_key=resolve_key)
```

- [ ] **Step 6: Run the reconcile + engine suites to verify they pass**

Run: `cd ui && python -m pytest tests/test_model_reconcile.py tests/test_config_engine.py -v`
Expected: PASS — new identity tests pass, updated reconcile tests pass, `diff_models` tests untouched-green, engine tests green.

- [ ] **Step 7: Commit**

```bash
git add ui/app/model_reconcile.py ui/app/config_engine.py ui/tests/test_model_reconcile.py
git commit -m "fix(reconcile): key desired by model_info.id + translate changed/credential signals to id-space"
```

---

### Task 2: Idempotent add (unique-constraint → update)

**Files:**
- Modify: `ui/app/model_reconcile.py` (the `to_add` loop)
- Test: `ui/tests/test_model_reconcile.py`

**Interfaces:**
- Consumes: `_is_already_exists` (defined in Task 1), `client.add_model`/`update_model`.
- Produces: a `to_add` that, on an "already exists" error, falls back to `update_model` (counted as `updated`), not a hard `failed`.

- [ ] **Step 1: Write the failing test**

Add to `ui/tests/test_model_reconcile.py`:

```python
class ConstraintOnAddClient(FakeModelsClient):
    async def add_model(self, p):
        import httpx
        req = httpx.Request("POST", "http://x/model/new")
        resp = httpx.Response(500, text='{"error":"Failed to add model to db. Check your server logs."}', request=req)
        raise httpx.HTTPStatusError("500", request=req, response=resp)


@pytest.mark.asyncio
async def test_reconcile_add_constraint_falls_back_to_update():
    client = ConstraintOnAddClient()
    items = [_model_item("m1")]                      # 'm1' not in live → planned as to_add
    rep = await reconcile_models(items, live=[], client=client,
                                 changed_item_names=set(), creds_changed=set(), resolve_key=lambda n: "sk")
    assert client.updated and client.updated[0]["model_info"]["id"] == "m1"   # fell back to update
    assert rep["added"] == 0 and rep["updated"] == 1 and rep["failed"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ui && python -m pytest tests/test_model_reconcile.py::test_reconcile_add_constraint_falls_back_to_update -v`
Expected: FAIL — the constraint error is recorded in `failed` (op `add`), `updated == 0`.

- [ ] **Step 3: Make the `to_add` loop idempotent**

In `ui/app/model_reconcile.py`, replace the `to_add` loop body in `reconcile_models`:

```python
    for entry in plan["to_add"]:
        try:
            await client.add_model(entry); added += 1
        except Exception as e:
            if _is_already_exists(e):
                try:
                    await client.update_model(entry); updated += 1
                except Exception as e2:
                    failed.append({"id": entry["model_info"]["id"], "op": "add->update", "error": str(e2)})
            else:
                failed.append({"id": entry["model_info"]["id"], "op": "add", "error": str(e)})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ui && python -m pytest tests/test_model_reconcile.py -v`
Expected: PASS (all reconcile tests, including the new constraint-fallback).

- [ ] **Step 5: Commit**

```bash
git add ui/app/model_reconcile.py ui/tests/test_model_reconcile.py
git commit -m "fix(reconcile): idempotent /model/new — unique-constraint falls back to update"
```

---

### Task 3: Full-suite gate + integration verification + release prep

**Files:** none modified — verification gate.

- [ ] **Step 1: Full backend suite green**

Run: `cd ui && python -m pytest tests/ -q`
Expected: all pass (no regressions; `diff_models`, engine, routes suites all green).

- [ ] **Step 2: Integration verification on a hybrid stack (controller-run)**

On a `STORE_MODEL_IN_DB=true` stack: (a) create a model item whose `data.model_info.id` ≠ its key, Apply → confirm it converges into `/model/info` with **no** `Unique constraint`/"Failed to add model to db" error in `litellm-proxy` logs; (b) rotate a referenced credential, Apply → confirm `models.updated ≥ 1` and the referencing model is re-pushed. (The live `.75` host already has the f0131005-class item resolved, so use a fresh local stack or a scratch item to reproduce.)

- [ ] **Step 3: Hand off**

Report: branch ready, full suite green, integration verified. The human merges → semantic-release cuts `1.21.1` + image; deploy to `.75` (`docker compose pull && up -d`) and bump the compose pin.

---

## Self-Review

**Spec coverage (§2):** desired keyed by `model_info.id` → Task 1 (`build_desired`). Signal translation (changed-models + rotated-credential models) → Task 1. Idempotent add → Task 2. Tests for all four §2.4 cases: name≠id keying (Task 1 `test_reconcile_keys_by_model_info_id_not_item_name`), credential-rotation force-update (Task 1), constraint→update (Task 2), no-wrongful-delete (covered by the name≠id no-op test — the live id is desired so never in `to_delete`). ✓

**Placeholder scan:** every step has exact code + commands. No TBDs.

**Type consistency:** `build_desired(items, resolve_key) -> (desired, name_to_id, failed)` used in Task 1; `reconcile_models(..., changed_item_names, creds_changed, resolve_key)` consistent across Tasks 1/2 and the `apply_config` call site; `_is_already_exists(e)` defined in Task 1, used in Task 2; `diff_models` signature unchanged.
