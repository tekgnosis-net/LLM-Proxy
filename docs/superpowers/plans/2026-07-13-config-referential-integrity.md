# Config Referential Integrity (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect → Prevent → Fix dangling config references (fallbacks / model_group_alias / per-key allowed-models & aliases that name a group which doesn't exist), so ordinary rename/delete operations can't silently leave orphans like the one that let the `hindsight` key reach `gpt-oss-20b-deepinfra`.

**Architecture:** One pure checker (`config_integrity.py`) is the single source of truth for "what is an orphan," shared by three consumers: a read-only report endpoint, a hard Apply-gate + key-save validator (prevent), and a per-orphan `dry_run` fix endpoint. References live in two stores (ours: `ui_config` router_settings; litellm's: `VerificationToken` keys) — that split only affects *where names come from*, never *how they're judged*.

**Tech Stack:** FastAPI + asyncpg (backend; tests via `ui/.venv/bin/python -m pytest`); Svelte 5 runes + Vite (`cd ui/frontend && npm run build`). NEVER use system `python3` for tests — it lacks fastapi; always `ui/.venv/bin/python`.

## Global Constraints

- **Valid group set `G`** = distinct `model_name` across effective (applied⊕staged) model items with `flag != "deleted"`. Works in hybrid and non-hybrid (model items live in `ui_config` either way).
- **Orphan rules (verbatim):** fallback (all variants) — every **primary** and **target** ∈ `G`; `model_group_alias` — every **target** ∈ `G` (alias *name* exempt); per-key `models[]` — each entry ∈ `G` **or** is one of that key's own `aliases` names (the 1.28.1 injection is legitimate, not an orphan); per-key `aliases{name:target}` — each **target** ∈ `G`.
- **Provider-stripping is NOT modelled here** — a reference is an orphan iff its literal name ∉ `G`. (Provider-strip collision detection is Phase 2.)
- **Apply-gate** raises `ApplyError` (→422) **pre-commit** (before write/fold), scope = router refs only. **Key-save validation** rejects (422) before passthrough to litellm.
- **Fix:** removal only (no repoint, no cascade). Router fix **stages** (needs Apply + ~25s restart); key fix is **hot** via `/key/update` (which REPLACES the sent field). `dry_run` returns before/after with no mutation.
- Errors follow the existing loud pattern: integrity endpoint returns `{"error":"query_failed"}` on key-store failure (never a false `in_sync`); malformed shapes are skipped by the pure checker, never a 500.
- Backend suite baseline is 237 passed / 1 skipped; keep it green and grow it.

---

### Task 1: `config_integrity.py` — the pure checker + trim helpers (TDD)

**Files:**
- Create: `ui/app/config_integrity.py`
- Create: `ui/tests/test_config_integrity.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces (Tasks 2–4 rely on these exact signatures):
  - `group_names(model_items: list[dict]) -> set[str]`
  - `router_orphans(router_items: list[dict], groups: set[str]) -> list[dict]`
  - `key_orphans(keys: list[dict], groups: set[str]) -> list[dict]`
  - `trim_router_setting(value, target: dict)` — returns the setting value with the dangling piece removed
  - `trim_key_field(value, target: dict)` — returns the `models`/`aliases` value with the dead entry removed
  - Orphan record shape: `{"scope","location","reference","missing":[...],"target":{...}}` (details in Step 3).

- [ ] **Step 1: Write the failing tests** — create `ui/tests/test_config_integrity.py`:

```python
from app.config_integrity import (group_names, router_orphans, key_orphans,
                                   trim_router_setting, trim_key_field)

def _model(name, mname, flag=None):
    it = {"kind": "model", "name": name, "data": {"model_name": mname}}
    if flag: it["flag"] = flag
    return it

def _rs(name, data, flag=None):
    it = {"kind": "router_setting", "name": name, "data": data}
    if flag: it["flag"] = flag
    return it

# ── group_names ─────────────────────────────────────────────────────────────
def test_group_names_dedups_and_skips_deleted():
    items = [_model("id1", "gpt-oss-20b-1x"), _model("id2", "gpt-oss-20b-1x"),
             _model("id3", "qwen3.6-27b-2x"), _model("id4", "gone", flag="deleted")]
    assert group_names(items) == {"gpt-oss-20b-1x", "qwen3.6-27b-2x"}

# ── router_orphans: fallbacks ───────────────────────────────────────────────
G = {"a", "b", "c"}
def test_fallback_primary_missing():
    o = router_orphans([_rs("fallbacks", [{"dead": ["a"]}])], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "fallbacks", "primary": "dead", "dangling": "dead"}

def test_fallback_target_missing():
    o = router_orphans([_rs("fallbacks", [{"a": ["b", "dead"]}])], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "fallbacks", "primary": "a", "dangling": "dead"}

def test_fallback_clean_no_orphans():
    assert router_orphans([_rs("fallbacks", [{"a": ["b"]}])], G) == []

def test_fallback_variants_scanned():
    for setting in ("context_window_fallbacks", "content_policy_fallbacks"):
        o = router_orphans([_rs(setting, [{"dead": ["a"]}])], G)
        assert len(o) == 1 and o[0]["target"]["setting"] == setting

def test_default_fallbacks_list_shape():
    o = router_orphans([_rs("default_fallbacks", ["a", "dead"])], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "default_fallbacks", "dangling": "dead"}

# ── router_orphans: model_group_alias ───────────────────────────────────────
def test_mga_target_missing():
    o = router_orphans([_rs("model_group_alias", {"myalias": "dead"})], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "model_group_alias", "alias": "myalias", "dangling": "dead"}

def test_mga_alias_name_exempt():
    # the alias NAME is a new public name; only the target must exist
    assert router_orphans([_rs("model_group_alias", {"newname": "a"})], G) == []

def test_router_deleted_setting_skipped_by_caller_contract():
    # caller passes non-deleted only; checker still tolerant of a stray flag
    assert router_orphans([_rs("fallbacks", [{"dead": ["a"]}], flag="deleted")], G) == \
           router_orphans([_rs("fallbacks", [{"dead": ["a"]}])], G)  # checker ignores flag

def test_router_malformed_shapes_never_raise():
    assert router_orphans([_rs("fallbacks", "not-a-list")], G) == []
    assert router_orphans([_rs("model_group_alias", ["not", "a", "dict"])], G) == []
    assert router_orphans([_rs("fallbacks", [{"a": "not-a-list"}])], G) == []

# ── key_orphans ─────────────────────────────────────────────────────────────
def test_key_allowed_model_missing():
    keys = [{"token": "h1", "key_alias": "ci", "models": ["a", "dead"], "aliases": {}}]
    o = key_orphans(keys, G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"token": "h1", "field": "models", "entry": "dead"}

def test_key_alias_name_in_models_is_exempt():
    # #25281 injection: an alias NAME legitimately appears in models
    keys = [{"token": "h1", "key_alias": "ci", "models": ["a", "myalias"],
             "aliases": {"myalias": "a"}}]
    assert key_orphans(keys, G) == []

def test_key_alias_target_missing():
    keys = [{"token": "h1", "key_alias": "ci", "models": [], "aliases": {"gpt-4": "dead"}}]
    o = key_orphans(keys, G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"token": "h1", "field": "aliases", "entry": "gpt-4", "dangling": "dead"}

def test_key_empty_models_means_all_allowed_no_orphan():
    assert key_orphans([{"token": "h1", "key_alias": "ci", "models": [], "aliases": {}}], G) == []

def test_key_malformed_never_raises():
    assert key_orphans([{"token": "h1", "models": None, "aliases": None}], G) == []

# ── trim helpers ────────────────────────────────────────────────────────────
def test_trim_router_drop_whole_rule_on_primary():
    v = [{"dead": ["a"]}, {"a": ["b"]}]
    assert trim_router_setting(v, {"setting": "fallbacks", "primary": "dead", "dangling": "dead"}) == [{"a": ["b"]}]

def test_trim_router_drop_only_target():
    v = [{"a": ["b", "dead"]}]
    assert trim_router_setting(v, {"setting": "fallbacks", "primary": "a", "dangling": "dead"}) == [{"a": ["b"]}]

def test_trim_router_empty_target_list_drops_rule():
    v = [{"a": ["dead"]}]
    assert trim_router_setting(v, {"setting": "fallbacks", "primary": "a", "dangling": "dead"}) == []

def test_trim_router_default_fallbacks_list():
    assert trim_router_setting(["a", "dead"], {"setting": "default_fallbacks", "dangling": "dead"}) == ["a"]

def test_trim_router_mga_entry():
    assert trim_router_setting({"x": "a", "y": "dead"}, {"setting": "model_group_alias", "alias": "y", "dangling": "dead"}) == {"x": "a"}

def test_trim_key_models():
    assert trim_key_field(["a", "dead"], {"field": "models", "entry": "dead"}) == ["a"]

def test_trim_key_aliases():
    assert trim_key_field({"gpt-4": "dead", "keep": "a"}, {"field": "aliases", "entry": "gpt-4"}) == {"keep": "a"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_integrity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config_integrity'`.

- [ ] **Step 3: Implement `ui/app/config_integrity.py`**

```python
from __future__ import annotations

# A model group is referenced from several places; a reference is an ORPHAN iff
# its literal name is not a current group (provider-stripping is Phase 2, not here).

_FALLBACK_RULE_SETTINGS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")


def group_names(model_items: list[dict]) -> set[str]:
    """Distinct public model_name across non-deleted model items (effective)."""
    return {(it.get("data") or {}).get("model_name")
            for it in model_items
            if it.get("kind") == "model" and it.get("flag") != "deleted"
            and (it.get("data") or {}).get("model_name")}


def _orphan(scope, location, reference, target):
    return {"scope": scope, "location": location, "reference": reference,
            "missing": [reference], "target": target}


def router_orphans(router_items: list[dict], groups: set[str]) -> list[dict]:
    """Scan fallback variants (list[{primary:[targets]}]), default_fallbacks (list[str]),
    and model_group_alias ({alias:target}). One orphan record per dangling name."""
    out: list[dict] = []
    for it in router_items:
        name, data = it.get("name"), it.get("data")
        if name in _FALLBACK_RULE_SETTINGS:
            if not isinstance(data, list):
                continue
            for rule in data:
                if not isinstance(rule, dict):
                    continue
                for primary, targets in rule.items():
                    if primary not in groups:
                        out.append(_orphan("router", f"router_settings.{name}", primary,
                                           {"setting": name, "primary": primary, "dangling": primary}))
                        continue                      # rule is doomed; don't also flag its targets
                    if not isinstance(targets, list):
                        continue
                    for t in targets:
                        if t not in groups:
                            out.append(_orphan("router", f"router_settings.{name}", t,
                                               {"setting": name, "primary": primary, "dangling": t}))
        elif name == "default_fallbacks":
            if not isinstance(data, list):
                continue
            for t in data:
                if t not in groups:
                    out.append(_orphan("router", "router_settings.default_fallbacks", t,
                                       {"setting": "default_fallbacks", "dangling": t}))
        elif name == "model_group_alias":
            if not isinstance(data, dict):
                continue
            for alias, target in data.items():
                if target not in groups:
                    out.append(_orphan("router", "router_settings.model_group_alias", target,
                                       {"setting": "model_group_alias", "alias": alias, "dangling": target}))
    return out


def key_orphans(keys: list[dict], groups: set[str]) -> list[dict]:
    """Per key: models[] entries not in G and not one of the key's own alias names;
    alias targets not in G. An empty models list means 'all allowed' — never an orphan."""
    out: list[dict] = []
    for k in keys or []:
        token = k.get("token")
        label = k.get("key_alias") or (token or "")[:10]
        aliases = k.get("aliases") if isinstance(k.get("aliases"), dict) else {}
        alias_names = set(aliases.keys())
        for m in (k.get("models") or []):
            if m and m not in groups and m not in alias_names:
                out.append(_orphan("key", f"key '{label}' → allowed models", m,
                                   {"token": token, "field": "models", "entry": m}))
        for alias_name, target in aliases.items():
            if target not in groups:
                out.append(_orphan("key", f"key '{label}' → alias '{alias_name}'", target,
                                   {"token": token, "field": "aliases", "entry": alias_name, "dangling": target}))
    return out


def trim_router_setting(value, target: dict):
    """Return `value` with the dangling piece removed, at the right granularity."""
    setting = target["setting"]
    if setting in _FALLBACK_RULE_SETTINGS:
        primary, dangling = target["primary"], target["dangling"]
        out = []
        for rule in (value or []):
            if not isinstance(rule, dict) or primary not in rule:
                out.append(rule); continue
            if dangling == primary:
                continue                              # drop the whole rule
            trimmed = [t for t in rule[primary] if t != dangling]
            if trimmed:
                out.append({**rule, primary: trimmed})
            # else: empty target list → drop the rule
        return out
    if setting == "default_fallbacks":
        return [t for t in (value or []) if t != target["dangling"]]
    if setting == "model_group_alias":
        return {a: t for a, t in (value or {}).items() if a != target["alias"]}
    return value


def trim_key_field(value, target: dict):
    """Return the key's models list / aliases dict with the dead entry removed."""
    if target["field"] == "models":
        return [m for m in (value or []) if m != target["entry"]]
    return {a: t for a, t in (value or {}).items() if a != target["entry"]}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_integrity.py -q` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/app/config_integrity.py ui/tests/test_config_integrity.py
git commit -m "feat: pure referential-integrity checker (orphan detection + trim helpers)"
```

---

### Task 2: Detect endpoint + Apply-gate (TDD)

**Files:**
- Modify: `ui/app/config_engine.py` (Apply-gate in `apply_config`)
- Modify: `ui/app/routes/config_v3_routes.py` (add `GET /api/config/integrity` + a `make_keys_client`)
- Modify: `ui/tests/test_config_v3_routes.py` (endpoint tests)
- Modify: `ui/tests/test_config_engine.py` (Apply-gate test)

**Interfaces:**
- Consumes: `group_names`, `router_orphans`, `key_orphans` (Task 1); `effective` (config_render); `ApplyError` (config_engine).
- Produces: `GET /api/config/integrity` → `{"in_sync", "router_orphans", "key_orphans"}` or `{"error":"query_failed", ...}`; `config_v3_routes.make_keys_client()` (monkeypatchable).

- [ ] **Step 1: Write the failing tests**

Add to `ui/tests/test_config_v3_routes.py` (uses the existing `_client`/`FakeStore`; extend FakeStore's applied to include a model + fallback):
```python
class FakeStoreWithModels(FakeStore):
    def __init__(self):
        super().__init__()
        self._applied = [
            {"kind": "model", "name": "id1", "data": {"model_name": "gpt-oss-20b-1x"}},
            {"kind": "router_setting", "name": "fallbacks", "data": [{"gpt-oss-20b": ["gpt-oss-20b-1x"]}]},
        ]
        self._staged = []

class FakeKeysV3:
    def __init__(self, keys): self._keys = keys
    async def list_keys(self): return self._keys

def _client_integ(tmp_path, store, keys):
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    cr.make_keys_client = lambda: FakeKeysV3(keys)
    return c

def test_integrity_reports_router_and_key_orphans(tmp_path):
    keys = [{"token": "h1", "key_alias": "ci", "models": ["deadgroup"], "aliases": {}}]
    d = _client_integ(tmp_path, FakeStoreWithModels(), keys).get("/api/config/integrity").json()
    assert d["in_sync"] is False
    assert any(o["reference"] == "gpt-oss-20b" for o in d["router_orphans"])   # fallback primary dead
    assert any(o["reference"] == "deadgroup" for o in d["key_orphans"])

def test_integrity_key_store_failure_is_loud(tmp_path):
    class Boom:
        async def list_keys(self): raise RuntimeError("proxy down")
    c = _client(tmp_path, FakeStoreWithModels())
    import app.routes.config_v3_routes as cr
    cr.make_keys_client = lambda: Boom()
    d = c.get("/api/config/integrity").json()
    assert d["error"] == "query_failed"
```

Add to `ui/tests/test_config_engine.py` (mirror its existing apply harness; the key assertion is that an orphaned fallback blocks apply pre-commit):
```python
import pytest
from app.config_engine import apply_config, ApplyError

class _Store:
    def __init__(self, items): self._items = items; self.folded = False
    async def applied(self): return list(self._items)
    async def staged(self): return []
    async def fold(self): self.folded = True

@pytest.mark.asyncio
async def test_apply_gate_blocks_orphaned_fallback(tmp_path):
    items = [{"kind": "model", "name": "id1", "data": {"model_name": "gpt-oss-20b-1x"}},
             {"kind": "router_setting", "name": "fallbacks", "data": [{"gpt-oss-20b": ["gpt-oss-20b-1x"]}]}]
    store = _Store(items)
    with pytest.raises(ApplyError) as e:
        await apply_config(str(tmp_path / "c.yaml"), store, reloader=None,
                           decrypt=lambda b: b, models_client=None, hybrid=False)
    assert "integrity" in str(e.value).lower() and not store.folded   # nothing committed
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py -k integrity tests/test_config_engine.py -k apply_gate -q`
Expected: FAIL (endpoint 404 / no gate).

- [ ] **Step 3a: Add the Apply-gate to `ui/app/config_engine.py`**

At the top, add the import:
```python
from app.config_integrity import group_names, router_orphans
```
In `apply_config`, immediately after `eff = effective(applied, staged)` (before the `if not hybrid:` branch), insert:
```python
    # Referential-integrity gate (pre-commit, both modes): a fallback / model_group_alias
    # that names a group which does not exist would render a dangling reference. Block
    # before any write/fold so nothing is committed.
    _groups = group_names([it for it in eff if it["kind"] == "model"])
    _orphans = router_orphans(
        [it for it in eff if it["kind"] == "router_setting" and it.get("flag") != "deleted"], _groups)
    if _orphans:
        detail = "; ".join(f'{o["location"]} references missing {o["reference"]!r}' for o in _orphans)
        raise ApplyError(f"integrity: {detail}; fix in the Integrity panel")
```

- [ ] **Step 3b: Add the endpoint + keys client to `ui/app/routes/config_v3_routes.py`**

Add imports near the top:
```python
from app.config_integrity import group_names, router_orphans, key_orphans
from app.keys_client import KeysClient
```
Add a monkeypatchable keys-client factory (next to `make_models_client`):
```python
def make_keys_client() -> KeysClient:
    s = get_settings()
    return KeysClient(s.litellm_base_url, s.litellm_master_key)
```
Add the endpoint (near `/config/drift`):
```python
@router.get("/config/integrity", dependencies=[Depends(login_required)])
async def config_integrity():
    store = make_config_store()
    eff = effective(await store.applied(), await store.staged())
    groups = group_names([i for i in eff if i["kind"] == "model"])
    r_orphans = router_orphans(
        [i for i in eff if i["kind"] == "router_setting" and i.get("flag") != "deleted"], groups)
    try:
        keys = await make_keys_client().list_keys()
    except Exception as e:
        return {"error": "query_failed", "detail": str(e), "router_orphans": r_orphans, "key_orphans": []}
    k_orphans = key_orphans(keys, groups)
    return {"in_sync": not r_orphans and not k_orphans,
            "router_orphans": r_orphans, "key_orphans": k_orphans}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py tests/test_config_engine.py -q` → PASS. Then full suite `cd ui && .venv/bin/python -m pytest tests/ -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add ui/app/config_engine.py ui/app/routes/config_v3_routes.py ui/tests/test_config_v3_routes.py ui/tests/test_config_engine.py
git commit -m "feat: integrity report endpoint + pre-commit Apply-gate for dangling router refs"
```

---

### Task 3: Key-save validation (TDD)

**Files:**
- Modify: `ui/app/routes/keys_routes.py` (validate `models`/`aliases` in `create_key` + `update_key`)
- Modify: `ui/tests/test_keys_routes.py`

**Interfaces:**
- Consumes: `group_names` (Task 1); `ConfigStore`/`effective`.
- Produces: `create_key`/`update_key` reject (422) a payload naming a group ∉ `G`; `keys_routes.make_config_store()` (monkeypatchable).

- [ ] **Step 1: Write the failing tests** — add to `ui/tests/test_keys_routes.py`:

```python
class FakeConfigStore:
    def __init__(self, groups):
        self._items = [{"kind": "model", "name": f"id{i}", "data": {"model_name": g}}
                       for i, g in enumerate(groups)]
    async def applied(self): return list(self._items)
    async def staged(self): return []

def _client_v(tmp_path, fake, groups):
    c = _client(tmp_path, fake)
    import app.routes.keys_routes as kr
    kr.make_config_store = lambda: FakeConfigStore(groups)
    return c

def test_create_key_rejects_dead_allowed_model(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["deadgroup"]})
    assert r.status_code == 422 and "deadgroup" in r.json()["detail"]

def test_create_key_allows_alias_name_in_models(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["gpt-oss-20b-1x", "myalias"],
                                  "aliases": {"myalias": "gpt-oss-20b-1x"}})
    assert r.status_code == 200                       # alias name legitimately in models

def test_update_key_rejects_dead_alias_target(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys/update", json={"key": "h1", "aliases": {"gpt-4": "deadgroup"}})
    assert r.status_code == 422 and "deadgroup" in r.json()["detail"]

def test_create_key_clean_passes(tmp_path):
    c = _client_v(tmp_path, FakeKeys(), groups=["gpt-oss-20b-1x"])
    r = c.post("/api/keys", json={"key_alias": "x", "models": ["gpt-oss-20b-1x"]})
    assert r.status_code == 200
```
(Extend `FakeKeys` with `async def update_key(self, payload): return {"updated": True, **payload}` if it isn't already present.)

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && .venv/bin/python -m pytest tests/test_keys_routes.py -k "reject or alias_name or clean_passes" -q`
Expected: FAIL (no validation yet → 200/502 instead of 422).

- [ ] **Step 3: Implement in `ui/app/routes/keys_routes.py`**

Add imports + factory + validator, and call it in both routes:
```python
from app.config_db import ConfigStore
from app.config_render import effective
from app.config_integrity import group_names


def make_config_store() -> ConfigStore:
    s = get_settings()
    return ConfigStore(s.database_url)


async def _validate_key_refs(payload: dict) -> None:
    """Reject a key whose models/aliases name a group that does not exist.
    An alias NAME may legitimately appear in models (the #25281 injection)."""
    s = get_settings()
    if not s.database_url:
        return                                        # no config store → skip (non-DB dev)
    store = make_config_store()
    eff = effective(await store.applied(), await store.staged())
    groups = group_names([i for i in eff if i["kind"] == "model"])
    alias_names = set((payload.get("aliases") or {}).keys())
    bad = [m for m in (payload.get("models") or []) if m and m not in groups and m not in alias_names]
    bad += [t for t in (payload.get("aliases") or {}).values() if t not in groups]
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"key references unknown model group(s): {', '.join(sorted(set(bad)))}")
```
In `create_key` and `update_key`, add `await _validate_key_refs(payload)` as the first line inside the `try:` (before the passthrough call). Let the `HTTPException` propagate (do not wrap it in the 502 handler — re-raise it):
```python
@router.post("/keys", dependencies=[Depends(login_required)])
async def create_key(payload: dict = Body(...)):
    await _validate_key_refs(payload)
    try:
        return await make_keys_client().generate_key(payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")
```
(Apply the identical pattern to `update_key`.)

- [ ] **Step 4: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_keys_routes.py -q` → PASS. Full suite → no regressions.

- [ ] **Step 5: Commit**

```bash
git add ui/app/routes/keys_routes.py ui/tests/test_keys_routes.py
git commit -m "feat: reject virtual keys that reference unknown model groups (models/aliases)"
```

---

### Task 4: Per-orphan fix endpoint (TDD)

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py` (add `POST /api/config/integrity/fix`)
- Modify: `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Consumes: `trim_router_setting`, `trim_key_field` (Task 1); `effective`; `make_config_store`, `make_keys_client` (Task 2). `FakeStore.stage` records `(kind,name,data,deleted)`; `FakeKeys.update_key` echoes payload.
- Produces: `POST /api/config/integrity/fix {orphan, dry_run}` → dry-run `{before,after,effect}`; router fix `{staged:True,needs_apply:True}`; key fix `{applied:True,needs_apply:False}`.

- [ ] **Step 1: Write the failing tests** — add to `ui/tests/test_config_v3_routes.py`:

```python
class FakeKeysFix(FakeKeysV3):
    def __init__(self, keys): super().__init__(keys); self.updated = None
    async def update_key(self, payload): self.updated = payload; return {"updated": True, **payload}

def test_fix_router_dry_run_previews_without_staging(tmp_path):
    store = FakeStoreWithModels()
    c = _client_integ(tmp_path, store, keys=[])
    orphan = {"scope": "router", "target": {"setting": "fallbacks", "primary": "gpt-oss-20b", "dangling": "gpt-oss-20b"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": True}).json()
    assert d["before"] == [{"gpt-oss-20b": ["gpt-oss-20b-1x"]}] and d["after"] == []
    assert "Apply" in d["effect"] and store.staged_calls == []       # nothing mutated on dry-run

def test_fix_router_stages_delete_when_setting_empties(tmp_path):
    store = FakeStoreWithModels()
    c = _client_integ(tmp_path, store, keys=[])
    orphan = {"scope": "router", "target": {"setting": "fallbacks", "primary": "gpt-oss-20b", "dangling": "gpt-oss-20b"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": False}).json()
    assert d == {"staged": True, "needs_apply": True}
    assert store.staged_calls == [("router_setting", "fallbacks", {}, True)]   # emptied → staged delete

def test_fix_key_updates_hot(tmp_path):
    keys = [{"token": "h1", "key_alias": "ci", "models": ["gpt-oss-20b-1x", "deadgroup"], "aliases": {}}]
    store = FakeStoreWithModels()
    c = _client(tmp_path, store)
    import app.routes.config_v3_routes as cr
    fake = FakeKeysFix(keys); cr.make_keys_client = lambda: fake
    orphan = {"scope": "key", "target": {"token": "h1", "field": "models", "entry": "deadgroup"}}
    d = c.post("/api/config/integrity/fix", json={"orphan": orphan, "dry_run": False}).json()
    assert d == {"applied": True, "needs_apply": False}
    assert fake.updated == {"key": "h1", "models": ["gpt-oss-20b-1x"]}   # trimmed, replace semantics
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py -k fix -q`
Expected: FAIL (endpoint 404).

- [ ] **Step 3: Implement in `ui/app/routes/config_v3_routes.py`**

Add `trim_router_setting, trim_key_field` to the `config_integrity` import, then add:
```python
@router.post("/config/integrity/fix", dependencies=[Depends(login_required)])
async def config_integrity_fix(body: dict = Body(...)):
    orphan = body.get("orphan") or {}
    dry = bool(body.get("dry_run"))
    scope = orphan.get("scope")
    target = orphan.get("target") or {}
    if scope == "router":
        store = make_config_store()
        eff = {(i["kind"], i["name"]): i for i in effective(await store.applied(), await store.staged())}
        it = eff.get(("router_setting", target.get("setting")))
        before = (it or {}).get("data")
        after = trim_router_setting(before, target)
        if dry:
            return {"before": before, "after": after,
                    "effect": "stages a config change (needs Apply + restart)"}
        if after in (None, [], {}):
            await store.stage("router_setting", target["setting"], {}, deleted=True)
        else:
            await store.stage("router_setting", target["setting"], after)
        return {"staged": True, "needs_apply": True}
    if scope == "key":
        keys = {k.get("token"): k for k in await make_keys_client().list_keys()}
        k = keys.get(target.get("token"))
        if k is None:
            raise HTTPException(status_code=409, detail="key not found (already changed?); re-scan")
        field = target["field"]
        before = k.get(field)
        after = trim_key_field(before, target)
        if dry:
            return {"before": before, "after": after, "effect": "applies immediately (hot)"}
        await make_keys_client().update_key({"key": target["token"], field: after})
        return {"applied": True, "needs_apply": False}
    raise HTTPException(status_code=422, detail="orphan.scope must be 'router' or 'key'")
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py -q` → PASS. Full suite → no regressions.

- [ ] **Step 5: Commit**

```bash
git add ui/app/routes/config_v3_routes.py ui/tests/test_config_v3_routes.py
git commit -m "feat: per-orphan integrity fix endpoint (dry-run preview; router stages, key hot)"
```

---

### Task 5: Frontend — Integrity panel + badge

**Files:**
- Modify: `ui/frontend/src/lib/api.js` (integrity + fix calls)
- Modify: `ui/frontend/src/routes/Routing.svelte` (panel + badge)

**Interfaces:**
- Consumes: `GET /api/config/integrity`, `POST /api/config/integrity/fix` (Tasks 2 & 4). `store.load()` / `store.applying` exist (used by Routing already).

- [ ] **Step 1: Add API calls** — in `ui/frontend/src/lib/api.js`, alongside `drift`/`resync`:

```js
  integrity: () => req('/api/config/integrity'),
  integrityFix: (orphan, dry_run) => req('/api/config/integrity/fix', { method: 'POST', body: JSON.stringify({ orphan, dry_run }) }),
```

- [ ] **Step 2: Add the panel to `ui/frontend/src/routes/Routing.svelte`**

In the `<script>`, add state + loader + fixer (place after the existing router-setting state):
```svelte
  import { api } from '../lib/api.js'   // ensure imported (Routing already uses store; add if missing)
  let integ = $state(null)
  let integBusy = $state(false)
  let integErr = $state('')
  async function loadIntegrity() {
    try { integ = await api.integrity(); integErr = integ?.error ? 'Integrity check failed (proxy/key API).' : '' }
    catch (e) { integErr = e.message }
  }
  onMount(loadIntegrity)
  async function fixOrphan(o) {
    integBusy = true
    try {
      const prev = await api.integrityFix(o, true)               // dry-run preview
      const msg = `Remove ${o.reference} from ${o.location}?\n\n` +
                  `${o.scope === 'router' ? 'Stages a change — needs Apply (restart).' : 'Applies immediately (hot).'}`
      if (!confirm(msg)) return
      await api.integrityFix(o, false)
      await loadIntegrity()
      if (o.scope === 'router') await store.load()               // reflect the newly-staged change
    } catch (e) { integErr = e.message }
    finally { integBusy = false }
  }
  let orphanCount = $derived((integ?.router_orphans?.length || 0) + (integ?.key_orphans?.length || 0))
```
Add the panel markup near the top of the page (after `<h1>Routing</h1>`):
```svelte
  <section class="card">
    <h2>Referential integrity
      {#if orphanCount > 0}<span class="badge-warn">{orphanCount}</span>{/if}</h2>
    {#if integErr}<div class="banner err">{integErr}</div>
    {:else if !integ}<p class="hint">Checking…</p>
    {:else if orphanCount === 0}<p class="hint">✓ No dangling references.</p>
    {:else}
      <p class="hint">These config references name a model group that doesn't exist. Removing them prevents unintended fallback routing.</p>
      <ul class="orphans">
        {#each [...(integ.router_orphans || []), ...(integ.key_orphans || [])] as o}
          <li>
            <span class="mono">{o.location}</span> → missing <span class="mono red">{o.reference}</span>
            <button onclick={() => fixOrphan(o)} disabled={integBusy}>Fix</button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
```
Add minimal styles (reuse existing `.card`/`.banner`/`.hint`/`.mono` if present; add only what's missing):
```svelte
  .badge-warn{background:#c0271d;color:#fff;border-radius:10px;padding:1px 8px;font-size:12px;margin-left:8px}
  .orphans{list-style:none;padding:0;margin:8px 0}
  .orphans li{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid rgba(0,0,0,.06);font-size:13px}
  .orphans button{margin-left:auto;font-size:12px;padding:3px 12px;border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer}
  .red{color:#c0271d}
```
(If `onMount` / `store` / `api` are already imported at the top of Routing.svelte, don't duplicate the imports — check first.)

- [ ] **Step 3: Build**

Run: `cd ui/frontend && npm run build` → `✓ built`, no errors.

- [ ] **Step 4: Commit**

```bash
git add ui/frontend/src/lib/api.js ui/frontend/src/routes/Routing.svelte
git commit -m "feat(ui): Routing integrity panel — lists dangling references with per-orphan Fix"
```

---

### Task 6: Docs, integration, release, deploy (controller)

**Files:**
- Modify: `docs/admin-ui-guide.md` (Routing → "Referential integrity" subsection), `docs/config-schema.md` (reference-rule note).

- [ ] **Step 1: Docs** — add to `docs/admin-ui-guide.md` under the Routing section:
```markdown
### Referential integrity

The Routing screen checks that every model-group reference in your config points
at a group that actually exists, and lists any that don't:

- **Global router refs** — `fallbacks` (and `context_window_fallbacks`,
  `content_policy_fallbacks`, `default_fallbacks`) and `model_group_alias`. A
  dangling one **blocks Apply** (a 422) until fixed — this is what prevents a
  renamed/removed group from leaving a fallback that silently routes elsewhere.
- **Virtual-key refs** — a key's allowed-models or aliases naming a group that no
  longer exists. New/edited keys with a dead reference are rejected at save.

Each listed orphan has a **Fix** button that removes just that dangling reference
(after a preview). Router fixes are **staged** — they need an Apply (proxy
restart); key fixes apply **immediately**. When everything resolves you'll see
"✓ No dangling references."

> This checks that references *resolve*. It does not (yet) analyse whether a key
> can reach a group indirectly via a deployment's provider-stripped base model —
> that reachability audit is a separate follow-up.
```
Add to `docs/config-schema.md` a one-line note on the fallback/model_group_alias sections: "Every fallback primary/target and every model_group_alias target must name a model group that exists; the admin UI blocks Apply otherwise."
Commit: `git add docs/admin-ui-guide.md docs/config-schema.md && git commit -m "docs: referential-integrity panel + reference rules"`

- [ ] **Step 2: Full suite + build** — `cd ui && .venv/bin/python -m pytest tests/ -q` (expect all pass, count grown by the new tests) and `cd ui/frontend && npm run build`.

- [ ] **Step 3: Local hybrid stack integration** — recreate `docker-compose.override.yml` (litellm+UI `STORE_MODEL_IN_DB: "true"`, UI `build: ./ui`), `docker compose up -d --build llm-proxy-ui`, wait healthy. Seed **one router orphan** and **one key orphan**:
```bash
PU=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)
docker exec -i litellm-postgres psql -U "$PU" -d litellm <<'SQL'
INSERT INTO ui_config_applied(kind,name,data) VALUES
 ('router_setting','fallbacks','[{"deadgroup":["gpt-4o"]}]')
 ON CONFLICT(kind,name) DO UPDATE SET data=EXCLUDED.data;
SQL
# create a key allowed a dead group via the litellm master key (kept on-host)
```
Drive the UI (browser-driven if Playwright available, else `browser_evaluate`/curl against the authed API): load Routing → integrity panel shows 2 orphans → Fix the router one (stages → Apply) → Fix the key one (hot) → re-scan shows "✓". Separately confirm Apply is blocked (422) while the orphaned fallback is staged. Clean up seeds + `docker compose down && rm docker-compose.override.yml`.

- [ ] **Step 4: Final whole-branch review** (opus, review-package over the branch) → fix Critical/Important → then finishing-a-development-branch: merge `--no-ff` to main (CI cuts the next minor), pull the release bot commit, bump the UI image pin, deploy to `.75` UI-only (litellm `StartedAt` unchanged — this is a UI-only change; no router-settings write, so no gateway restart), update memory.

---

## Self-Review

**Spec coverage:** pure checker with all four orphan rules + alias-name exemption + malformed tolerance (T1) ✓; `G` = non-deleted effective model names (T1 `group_names`) ✓; detect endpoint + `query_failed` guard (T2) ✓; Apply-gate pre-commit both modes, router-scope (T2) ✓; key-save validation 422 (T3) ✓; per-orphan dry-run fix, router-stages/key-hot, granular trim, stage-delete-when-empty (T4) ✓; UI panel + badge + preview-confirm (T5) ✓; docs incl. the "resolves ≠ contained" caveat (T6) ✓; Playwright/integration incl. Apply-block (T6) ✓; Phase-2 explicitly excluded (no provider-strip logic anywhere) ✓.

**Placeholder scan:** none — every code step carries full code; integration step names its fallback (browser_evaluate) if Playwright is unavailable.

**Type consistency:** orphan record `{scope,location,reference,missing,target}` and the two `target` shapes (`{setting,primary,dangling}` / `{setting,alias,dangling}` / `{setting,dangling}` for router; `{token,field,entry[,dangling]}` for key) are produced in T1 and consumed unchanged by the T4 fix + T5 UI; `group_names`/`router_orphans`/`key_orphans`/`trim_router_setting`/`trim_key_field` signatures match across T1–T4; `make_keys_client` added in T2 is reused in T4; `make_config_store` added in T3 is local to keys_routes.
