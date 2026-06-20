# Hybrid Hot-Apply Engine + ui_config Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make model add/edit/delete apply **hot** (no container restart) via LiteLLM's `/model/*` API while settings keep the restart path, and add a `ui_config` export as the new reproducibility artifact.

**Architecture:** With `STORE_MODEL_IN_DB=true`, LiteLLM serves models from its own DB. The UI's `ui_config` stays the master; on Apply we **split-render**: models reconcile declaratively against `/model/info` (add/update/delete by `model_info.id`) with their keys resolved-and-inlined from the vault, while router/litellm/general/passthrough settings render to a **settings-only, secret-free `config.yaml`** and restart only when one actually changed. Credentials apply hot (re-inline into affected models); migration is empty-then-fill so a model is never in YAML and the DB at once.

**Tech Stack:** FastAPI + asyncpg + httpx (`httpx.MockTransport` for client tests), the existing `ui_config` store (`ConfigStore`), Svelte 5 frontend, Docker Compose (`STORE_MODEL_IN_DB` env), Playwright for integration.

## Global Constraints

- Source spec: `docs/superpowers/specs/2026-06-20-hybrid-hot-apply-design.md`. Implement the `1.21.0` scope (hybrid engine + export). The `1.20.0` slice (auto-refresh, health control) is a **separate, already-planned** effort — do not duplicate it.
- **Forks are LOCKED — do not reopen:** (a) **Credentials:** API-pushed models carry their key **inline**, resolved server-side from the vault; `config.yaml` in hybrid mode renders **neither `model_list` nor `credential_list`** (fully secret-free); we never rely on LiteLLM resolving `litellm_credential_name` for DB models. (b) **Migration:** **empty-then-fill** — config.yaml has zero models before the `STORE_MODEL_IN_DB=true` start, then reconcile fills the empty DB.
- **Security (verbatim, non-negotiable):** Never commit `.env`. Never rotate `LITELLM_SALT_KEY` (encrypts LiteLLM-DB model keys) or `SESSION_SECRET`/`credentials_key` (derives the vault Fernet key) after keys are saved. `config.yaml` must hold no literal secrets (the existing `_check_no_literal_secrets` guard stays; in hybrid mode the file is secret-free anyway). Master key stays server-side only. Don't mutate the live host DB unprompted.
- **TDD for backend:** every backend task writes a failing test first using the established patterns — `FakeStore`/`FakeReloader` (see `ui/tests/test_config_engine.py`), `httpx.MockTransport(handler)` (see `ui/tests/test_keys_client.py`), pure-function golden tests (see `ui/tests/test_config_render.py`). Run backend tests with `cd ui && python -m pytest tests/<file> -v`.
- **Frontend has no unit harness** — verify with `cd ui/frontend && npm run build` + Playwright on the LAN IP **`http://10.0.20.85:8081`** (never localhost; non-secure-context APIs differ). Local stack admin password: `Smoke-Admin-2026`.
- Backwards compatibility: `render_config(...)` and `apply_config(...)` default to **non-hybrid** behavior. Every existing test in `ui/tests/` must stay green.
- Do not push or release — the human merges to `main` (cuts version + image) and pins it.

---

## File Structure

- `ui/app/config_render.py` — extract `render_model_entry(it, resolve_key=None)`; add `hybrid` param to `render_config`. (Task 2)
- `ui/app/models_client.py` *(new)* — `ModelsClient` (`/model/info`, `/model/new`, `/model/update`, `/model/delete`), mirroring `keys_client.py`. (Task 3)
- `ui/app/model_reconcile.py` *(new)* — pure `diff_models(...)` + async `reconcile_models(...)`. Kept separate from the engine so the diff is unit-testable in isolation. (Task 4)
- `ui/app/config_engine.py` — `apply_config` gains a hybrid branch (settings/creds split, reconcile, merged report) + `_make_resolve_key`. (Task 5)
- `ui/app/settings.py` — add `store_model_in_db: bool`. `ui/app/routes/config_v3_routes.py` — construct `ModelsClient`, pass `hybrid`. (Task 6)
- `docker-compose.yml` / `.env.example` — wire `STORE_MODEL_IN_DB` to both `litellm` and `ui`. `ui/frontend/src/routes/Models.svelte` / `Settings.svelte` — apply-banner copy. (Task 6)
- `ui/app/routes/config_v3_routes.py` — `POST /api/config/prepare-hot-apply`; `ui/frontend/src/routes/Settings.svelte` — "Enable hot-apply" card + runbook. (Task 7)
- `ui/app/routes/config_v3_routes.py` — `GET /api/config/export`; `ui/frontend/src/routes/Settings.svelte` — retarget the export link. (Task 8)

---

### Task 1: Confirm LiteLLM model-API behavior on a real stack (integration, run FIRST)

**Files:** none (a throwaway local stack + recorded findings). This produces the exact request/response shapes Tasks 3–4 depend on, and decides one contingency (whether `/model/update` works or we use delete+add).

**Why first:** the forks are locked so the *design* doesn't branch, but the `ModelsClient` payload shapes must match LiteLLM exactly. Confirm them once before writing the client.

- [ ] **Step 1: Bring up a local stack with `STORE_MODEL_IN_DB=true`**

In a scratch copy of the stack, set the litellm service env `STORE_MODEL_IN_DB: "true"` and start postgres + litellm. Export `MK=$LITELLM_MASTER_KEY` and `BASE=http://10.0.20.85:<litellm-port>`.

- [ ] **Step 2: Hot add → appears with no restart**

```bash
curl -s -X POST "$BASE/model/new" -H "Authorization: Bearer $MK" -H 'Content-Type: application/json' \
  -d '{"model_name":"probe","litellm_params":{"model":"openai/gpt-4o","api_key":"sk-test"},"model_info":{"id":"probe-uuid-1"}}'
curl -s "$BASE/v1/models" -H "Authorization: Bearer $MK"
```
Expected: the second call lists `probe-uuid-1` **without** a restart. Record the exact `/model/new` success body.

- [ ] **Step 3: `/model/info` shape (id echo)**

```bash
curl -s "$BASE/model/info" -H "Authorization: Bearer $MK"
```
Expected: a `{"data":[{...,"model_info":{"id":"probe-uuid-1",...}}]}` (or bare list). Record whether it's wrapped in `data` and that `model_info.id` echoes ours, and how `api_key` appears (masked/absent — Task 4 excludes it from comparison anyway).

- [ ] **Step 4: Update + delete are hot**

```bash
curl -s -X POST "$BASE/model/update" -H "Authorization: Bearer $MK" -H 'Content-Type: application/json' \
  -d '{"model_name":"probe","litellm_params":{"model":"openai/gpt-4o-mini","api_key":"sk-test"},"model_info":{"id":"probe-uuid-1"}}'
curl -s -X POST "$BASE/model/delete" -H "Authorization: Bearer $MK" -H 'Content-Type: application/json' -d '{"id":"probe-uuid-1"}'
curl -s "$BASE/v1/models" -H "Authorization: Bearer $MK"
```
Expected: update succeeds (model string changes) and delete removes it, both live. **Contingency:** if `/model/update` errors or no-ops, record it — Task 4's `to_update` then uses delete-then-add instead (the client supports both).

- [ ] **Step 5: Survives restart + empty-config = DB-only**

Restart the litellm container; confirm `probe` (re-added) persists. Set the config.yaml `model_list: []`, restart, and confirm `/v1/models` returns exactly the DB models (no phantom from the empty config, no duplication).

- [ ] **Step 6: Record findings**

Append a short "LiteLLM model-API findings (verified <date>)" note to the spec or this plan: the confirmed `/model/new`,`/model/update`,`/model/delete`,`/model/info` shapes and the update-vs-delete+add decision. No commit of stack files.

---

### Task 2: `render_model_entry` extraction + `render_config(hybrid=…)`

**Files:**
- Modify: `ui/app/config_render.py:36-71`
- Test: `ui/tests/test_config_render.py`

**Interfaces:**
- Produces: `render_model_entry(it: dict, resolve_key: Optional[Callable[[str], Optional[str]]] = None) -> dict` — builds a LiteLLM model entry from a `ui_config` model item (`model_info.id` defaults to `it["name"]`). When `resolve_key` is given, it inlines the credential: pops `litellm_params.litellm_credential_name`, calls `resolve_key(name)`, sets `litellm_params.api_key`; raises `KeyError` if `resolve_key` returns `None`. `os.environ/…` keys pass through untouched.
- Produces: `render_config(items, decrypt, hybrid: bool = False)` — `hybrid=False` is today's behavior; `hybrid=True` omits both `model_list` (rendered as `[]`) and `credential_list`.
- Consumes: nothing new.

- [ ] **Step 1: Write failing tests for `render_model_entry`**

Add to `ui/tests/test_config_render.py`:

```python
from app.config_render import render_model_entry


def _model_item(cred=None, env=None):
    lp = {"model": "openai/gpt-4o"}
    if cred: lp["litellm_credential_name"] = cred
    if env: lp["api_key"] = env
    return {"kind": "model", "name": "uuid-1",
            "data": {"model_name": "gpt-4o", "litellm_params": lp, "model_info": {"mode": "chat"}}}


def test_render_model_entry_sets_id_and_shape():
    e = render_model_entry(_model_item())
    assert e["model_name"] == "gpt-4o"
    assert e["model_info"]["id"] == "uuid-1"
    assert e["model_info"]["mode"] == "chat"
    assert e["litellm_params"] == {"model": "openai/gpt-4o"}


def test_render_model_entry_no_resolve_keeps_credential_name():
    e = render_model_entry(_model_item(cred="openai"))
    assert e["litellm_params"]["litellm_credential_name"] == "openai"
    assert "api_key" not in e["litellm_params"]


def test_render_model_entry_inlines_credential():
    e = render_model_entry(_model_item(cred="openai"), resolve_key=lambda n: "sk-REAL")
    assert e["litellm_params"]["api_key"] == "sk-REAL"
    assert "litellm_credential_name" not in e["litellm_params"]


def test_render_model_entry_missing_credential_raises():
    import pytest
    with pytest.raises(KeyError):
        render_model_entry(_model_item(cred="ghost"), resolve_key=lambda n: None)


def test_render_model_entry_env_key_passes_through():
    e = render_model_entry(_model_item(env="os.environ/OPENAI_API_KEY"), resolve_key=lambda n: "sk-REAL")
    assert e["litellm_params"]["api_key"] == "os.environ/OPENAI_API_KEY"   # no cred name → no inline
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && python -m pytest tests/test_config_render.py -k render_model_entry -v`
Expected: FAIL — `ImportError: cannot import name 'render_model_entry'`.

- [ ] **Step 3: Implement `render_model_entry` and refactor `render_config`**

In `ui/app/config_render.py`, replace the `render_config` model branch with a call to a new helper, and add `hybrid`:

```python
def render_model_entry(it, resolve_key=None):
    """Build a LiteLLM model entry from a ui_config model item. model_info.id
    defaults to the item name (UUID). When resolve_key is given, inline the
    credential key (hybrid path): litellm_credential_name -> api_key."""
    data, name = it["data"], it["name"]
    entry = {"model_name": data.get("model_name", name)}
    mi = dict(data.get("model_info") or {})
    mi.setdefault("id", name)
    entry.update({k: v for k, v in data.items() if k not in ("model_name", "model_info")})
    entry["model_info"] = mi
    if resolve_key is not None:
        lp = dict(entry.get("litellm_params") or {})
        cred_name = lp.pop("litellm_credential_name", None)
        if cred_name:
            key = resolve_key(cred_name)
            if key is None:
                raise KeyError(f"credential {cred_name!r} not found")
            lp["api_key"] = key
        entry["litellm_params"] = lp
    return entry


def render_config(items, decrypt, hybrid=False):
    base = {}
    model_list, credential_list = [], []
    sections = {}
    for it in items:
        if it.get("flag") == "deleted":
            continue
        kind, name, data = it["kind"], it["name"], it["data"]
        if kind == "passthrough":
            base = copy.deepcopy(data) if isinstance(data, dict) else {}
        elif kind == "model":
            if not hybrid:
                model_list.append(render_model_entry(it))
        elif kind == "credential":
            if not hybrid:
                credential_list.append({"credential_name": name,
                                        "credential_values": {"api_key": decrypt(data.get("value_encrypted"))},
                                        "credential_info": {"provider": data.get("provider")}})
        elif kind in _SECTION_BY_KIND:
            sections.setdefault(_SECTION_BY_KIND[kind], {})[name] = data
    cfg = base
    for sec, kv in sections.items():
        cfg[sec] = _deep_merge(base.get(sec, {}), kv) if isinstance(base.get(sec), dict) else dict(kv)
    if hybrid:
        cfg.setdefault("model_list", [])      # empty: LiteLLM serves DB models only; no credential_list
    else:
        if model_list:
            cfg["model_list"] = model_list
        else:
            cfg.setdefault("model_list", [])
        if credential_list:
            cfg["credential_list"] = credential_list
    return cfg
```

Keep `_SECTION_BY_KIND`, `_deep_merge`, `redact_rendered` unchanged. Ensure `from typing import Callable, Optional` covers the annotations (already imported).

- [ ] **Step 4: Write failing test for hybrid render**

Add to `ui/tests/test_config_render.py`:

```python
def test_render_hybrid_omits_models_and_credentials():
    items = [
        {"kind": "model", "name": "uuid-1", "data": {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "openai"}}, "flag": None},
        {"kind": "credential", "name": "openai", "data": {"provider": "openai", "value_encrypted": "ENC"}, "flag": None},
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": None},
    ]
    cfg = render_config(items, decrypt=lambda b: "sk-REAL", hybrid=True)
    assert cfg["model_list"] == []
    assert "credential_list" not in cfg
    assert cfg["router_settings"] == {"routing_strategy": "least-busy"}
```

- [ ] **Step 5: Run the full render test file**

Run: `cd ui && python -m pytest tests/test_config_render.py -v`
Expected: PASS — new tests pass AND the pre-existing `test_render_groups_items_into_sections_and_decrypts_creds` / `test_two_models_same_name_both_render` / `test_model_render_sets_model_info_id_to_item_uuid` still pass (back-compat: default `hybrid=False`).

- [ ] **Step 6: Commit**

```bash
git add ui/app/config_render.py ui/tests/test_config_render.py
git commit -m "feat(render): extract render_model_entry (key inlining) + hybrid render mode"
```

---

### Task 3: `ModelsClient` (LiteLLM `/model/*` API)

**Files:**
- Create: `ui/app/models_client.py`
- Test: `ui/tests/test_models_client.py`

**Interfaces:**
- Produces: `ModelsClient(base_url, master_key, transport=None)` with async `list_models() -> list[dict]` (GET `/model/info`, unwraps `{"data":[…]}`), `add_model(payload) -> dict` (POST `/model/new`), `update_model(payload) -> dict` (POST `/model/update`), `delete_model(model_id: str) -> dict` (POST `/model/delete` with `{"id": model_id}`). All raise on HTTP ≥ 400 (`raise_for_status`).
- Consumes: nothing (mirrors `keys_client.py`).

- [ ] **Step 1: Write failing tests**

Create `ui/tests/test_models_client.py`:

```python
import json, httpx, pytest
from app.models_client import ModelsClient


def _client(handler):
    return ModelsClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_models_unwraps_data_and_auth():
    def handler(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        assert req.url.path.endswith("/model/info")
        return httpx.Response(200, json={"data": [{"model_name": "gpt", "model_info": {"id": "uuid-1"}}]})
    out = await _client(handler).list_models()
    assert out[0]["model_info"]["id"] == "uuid-1"


@pytest.mark.asyncio
async def test_add_model_posts_to_model_new():
    seen = {}
    def handler(req):
        seen["path"] = req.url.path; seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"model_id": "uuid-1"})
    await _client(handler).add_model({"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {"id": "uuid-1"}})
    assert seen["path"].endswith("/model/new")
    assert seen["body"]["model_info"]["id"] == "uuid-1"


@pytest.mark.asyncio
async def test_delete_model_posts_id():
    seen = {}
    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"deleted": True})
    await _client(handler).delete_model("uuid-1")
    assert seen["body"] == {"id": "uuid-1"}


@pytest.mark.asyncio
async def test_error_raises():
    def handler(req):
        return httpx.Response(500, text="boom")
    with pytest.raises(httpx.HTTPError):
        await _client(handler).list_models()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && python -m pytest tests/test_models_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models_client'`.

- [ ] **Step 3: Implement `ModelsClient`**

Create `ui/app/models_client.py`:

```python
from __future__ import annotations
import httpx
from typing import Any, Optional


class ModelsClient:
    """Async client for LiteLLM model-management endpoints (requires
    STORE_MODEL_IN_DB=true on the proxy). Master key stays server-side."""

    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def list_models(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/model/info")
            r.raise_for_status()
            data = r.json()
            return data.get("data", data) if isinstance(data, dict) else data

    async def add_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/model/new", json=payload)
            r.raise_for_status()
            return r.json()

    async def update_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/model/update", json=payload)
            r.raise_for_status()
            return r.json()

    async def delete_model(self, model_id: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/model/delete", json={"id": model_id})
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && python -m pytest tests/test_models_client.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add ui/app/models_client.py ui/tests/test_models_client.py
git commit -m "feat: ModelsClient for LiteLLM /model/* hot-apply API"
```

---

### Task 4: Declarative reconcile (`diff_models` + `reconcile_models`)

**Files:**
- Create: `ui/app/model_reconcile.py`
- Test: `ui/tests/test_model_reconcile.py`

**Interfaces:**
- Produces (pure): `diff_models(desired: dict[str,dict], live: list[dict], changed_ids: set[str], force_ids: set[str]) -> dict` → `{"to_add": [entry…], "to_update": [entry…], "to_delete": [id…]}`. `to_add`/`to_delete` by set-difference on `model_info.id` (declarative, drift-healing); `to_update` = ids present in both that are in `changed_ids ∪ force_ids` (staged-changed or credential-rotated) — **no fragile field compare** (LiteLLM masks `api_key` and injects defaults in `/model/info`).
- Produces (async): `reconcile_models(desired_items, live, client, changed_ids, force_ids, resolve_key) -> dict` → `{"added","updated","deleted","failed":[{id,op,error}]}`. Builds the desired map via `render_model_entry(it, resolve_key)`; a missing credential becomes a `failed` entry (op `resolve`), never a keyless push.
- Consumes: `render_model_entry` (Task 2); a `client` with `add_model/update_model/delete_model` (Task 3).

- [ ] **Step 1: Write failing tests for `diff_models`**

Create `ui/tests/test_model_reconcile.py`:

```python
import pytest
from app.model_reconcile import diff_models, reconcile_models


def _entry(i): return {"model_name": i, "litellm_params": {"model": "openai/x", "api_key": "sk"}, "model_info": {"id": i}}
def _live(i):  return {"model_name": i, "litellm_params": {"model": "openai/x", "api_key": "**masked**"}, "model_info": {"id": i}}


def test_diff_add_and_delete_by_id():
    desired = {"a": _entry("a"), "b": _entry("b")}
    live = [_live("b"), _live("c")]
    d = diff_models(desired, live, changed_ids=set(), force_ids=set())
    assert [e["model_info"]["id"] for e in d["to_add"]] == ["a"]
    assert d["to_delete"] == ["c"]
    assert d["to_update"] == []           # b in both but not changed/forced → no update


def test_diff_update_only_when_changed_or_forced():
    desired = {"a": _entry("a"), "b": _entry("b")}
    live = [_live("a"), _live("b")]
    d = diff_models(desired, live, changed_ids={"a"}, force_ids={"b"})
    assert sorted(e["model_info"]["id"] for e in d["to_update"]) == ["a", "b"]
    assert d["to_add"] == [] and d["to_delete"] == []


def test_diff_noop():
    desired = {"a": _entry("a")}
    d = diff_models(desired, [_live("a")], changed_ids=set(), force_ids=set())
    assert d == {"to_add": [], "to_update": [], "to_delete": []}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ui && python -m pytest tests/test_model_reconcile.py -k diff -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.model_reconcile'`.

- [ ] **Step 3: Implement `diff_models`**

Create `ui/app/model_reconcile.py`:

```python
from __future__ import annotations
from typing import Any, Callable, Optional

from app.config_render import render_model_entry


def _live_ids(live: list[dict]) -> set[str]:
    return {m.get("model_info", {}).get("id") for m in live if (m.get("model_info") or {}).get("id")}


def diff_models(desired: dict[str, dict], live: list[dict],
                changed_ids: set[str], force_ids: set[str]) -> dict[str, Any]:
    """Declarative add/delete by id (self-healing); update only for ids we know
    changed (staged 'changed') or whose credential rotated (force_ids)."""
    live_ids = _live_ids(live)
    desired_ids = set(desired)
    to_add = [desired[i] for i in sorted(desired_ids - live_ids)]
    to_delete = sorted(live_ids - desired_ids)
    upd_ids = (changed_ids | force_ids) & (desired_ids & live_ids)
    to_update = [desired[i] for i in sorted(upd_ids)]
    return {"to_add": to_add, "to_update": to_update, "to_delete": to_delete}
```

- [ ] **Step 4: Write failing tests for `reconcile_models`**

Append to `ui/tests/test_model_reconcile.py`:

```python
class FakeModelsClient:
    def __init__(self): self.added = []; self.updated = []; self.deleted = []
    async def add_model(self, p): self.added.append(p); return {}
    async def update_model(self, p): self.updated.append(p); return {}
    async def delete_model(self, i): self.deleted.append(i); return {}


def _model_item(name, cred=None):
    lp = {"model": "openai/gpt-4o"}
    if cred: lp["litellm_credential_name"] = cred
    return {"kind": "model", "name": name, "data": {"model_name": name, "litellm_params": lp, "model_info": {}}, "flag": None}


@pytest.mark.asyncio
async def test_reconcile_adds_and_inlines_key():
    client = FakeModelsClient()
    items = [_model_item("a", cred="openai")]
    rep = await reconcile_models(items, live=[], client=client,
                                 changed_ids=set(), force_ids=set(), resolve_key=lambda n: "sk-REAL")
    assert rep["added"] == 1 and rep["failed"] == []
    assert client.added[0]["litellm_params"]["api_key"] == "sk-REAL"
    assert "litellm_credential_name" not in client.added[0]["litellm_params"]


@pytest.mark.asyncio
async def test_reconcile_missing_credential_reported_not_pushed():
    client = FakeModelsClient()
    items = [_model_item("a", cred="ghost")]
    rep = await reconcile_models(items, live=[], client=client,
                                 changed_ids=set(), force_ids=set(), resolve_key=lambda n: None)
    assert client.added == []
    assert rep["added"] == 0
    assert rep["failed"][0]["id"] == "a" and rep["failed"][0]["op"] == "resolve"


@pytest.mark.asyncio
async def test_reconcile_deletes_drifted_live_model():
    client = FakeModelsClient()
    live = [{"model_name": "z", "model_info": {"id": "z"}}]
    rep = await reconcile_models([], live=live, client=client,
                                 changed_ids=set(), force_ids=set(), resolve_key=lambda n: "")
    assert client.deleted == ["z"] and rep["deleted"] == 1
```

- [ ] **Step 5: Run to verify they fail**

Run: `cd ui && python -m pytest tests/test_model_reconcile.py -k reconcile -v`
Expected: FAIL — `reconcile_models` not defined.

- [ ] **Step 6: Implement `reconcile_models`**

Append to `ui/app/model_reconcile.py`:

```python
async def reconcile_models(desired_items: list[dict], live: list[dict], client,
                           changed_ids: set[str], force_ids: set[str],
                           resolve_key: Callable[[str], Optional[str]]) -> dict[str, Any]:
    desired: dict[str, dict] = {}
    failed: list[dict] = []
    for it in desired_items:
        try:
            desired[it["name"]] = render_model_entry(it, resolve_key)
        except KeyError as e:
            failed.append({"id": it["name"], "op": "resolve", "error": str(e)})
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

> **Task 1 contingency:** if Step-4 findings showed `/model/update` unreliable, change the `to_update` loop to delete-then-add: `await client.delete_model(entry["model_info"]["id"]); await client.add_model(entry)`.

- [ ] **Step 7: Run the full reconcile test file**

Run: `cd ui && python -m pytest tests/test_model_reconcile.py -v`
Expected: PASS (6 tests).

- [ ] **Step 8: Commit**

```bash
git add ui/app/model_reconcile.py ui/tests/test_model_reconcile.py
git commit -m "feat: declarative model reconcile (diff + inline-key apply)"
```

---

### Task 5: `apply_config` hybrid branch

**Files:**
- Modify: `ui/app/config_engine.py`
- Test: `ui/tests/test_config_engine.py`

**Interfaces:**
- Produces: `apply_config(config_path, store, reloader, *, decrypt, models_client=None, hybrid=False) -> dict`. Non-hybrid path is unchanged (default). Hybrid path: `settings_changed` = any staged item in `{router_setting,litellm_setting,general_setting,passthrough}`; `creds_changed` = staged credential names; `changed_ids` = staged model names with flag `new`/`changed`. Pre-commit renders+validates+writes the settings-only config **only if** `settings_changed`; commits via `fold()`; post-commit runs `reconcile_models` then restarts **only if** `settings_changed`. Returns `{"applied": True, "hybrid": True, "models": <report>, "restart": "healthy"|"unhealthy"|"skipped", "detail"?: str}`.
- Consumes: `render_config(..., hybrid=True)` (Task 2), `reconcile_models` (Task 4), `effective`/`validate_config`/`write_config_atomic`/`store.fold` (existing).

- [ ] **Step 1: Write failing tests (extend the engine suite)**

Add to `ui/tests/test_config_engine.py`:

```python
from app.config_engine import apply_config as _apply  # already imported above; alias for clarity


class FakeModelsClient:
    def __init__(self): self.added=[]; self.updated=[]; self.deleted=[]
    async def list_models(self): return []
    async def add_model(self, p): self.added.append(p); return {}
    async def update_model(self, p): self.updated.append(p); return {}
    async def delete_model(self, i): self.deleted.append(i); return {}


class ModelStagedStore(FakeStore):
    """One staged NEW model, no settings staged."""
    def __init__(self):
        self._applied = []
        self._staged = [{"kind": "model", "name": "uuid-1",
                         "data": {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {}},
                         "flag": "new"}]
        self.folded = False
    async def fold(self): self.folded = True; self._staged = []


@pytest.mark.asyncio
async def test_hybrid_model_only_no_restart(tmp_path):
    store = ModelStagedStore(); mc = FakeModelsClient(); rl = FakeReloader(ok=True)
    p = str(tmp_path / "config.yaml")
    res = await apply_config(p, store, rl, decrypt=lambda b: "", models_client=mc, hybrid=True)
    assert res["restart"] == "skipped"          # no settings staged → no restart
    assert rl.calls == 0
    assert res["models"]["added"] == 1
    assert mc.added[0]["model_info"]["id"] == "uuid-1"
    assert store.folded is True
    from pathlib import Path
    assert not Path(p).exists()                  # no settings change → no file write


@pytest.mark.asyncio
async def test_hybrid_settings_change_writes_and_restarts(tmp_path):
    store = FakeStore(); mc = FakeModelsClient(); rl = FakeReloader(ok=True)  # FakeStore stages a router_setting
    p = str(tmp_path / "config.yaml")
    res = await apply_config(p, store, rl, decrypt=lambda b: "", models_client=mc, hybrid=True)
    assert res["restart"] == "healthy"
    assert rl.calls == 1
    import yaml
    cfg = yaml.safe_load(open(p))
    assert cfg["router_settings"]["routing_strategy"] == "least-busy"
    assert cfg["model_list"] == []               # hybrid: settings-only, models not in yaml
    assert "credential_list" not in cfg
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ui && python -m pytest tests/test_config_engine.py -k hybrid -v`
Expected: FAIL — `apply_config()` got an unexpected keyword argument `models_client`.

- [ ] **Step 3: Implement the hybrid branch + `_make_resolve_key`**

In `ui/app/config_engine.py`, add the import and the helper, and branch `apply_config`:

```python
# config_engine.py already imports `effective, render_config` from app.config_render — leave that line as-is.
from app.model_reconcile import reconcile_models   # add this import

_RESTART_KINDS = {"router_setting", "litellm_setting", "general_setting", "passthrough"}


def _make_resolve_key(eff, decrypt):
    creds = {it["name"]: it for it in eff
             if it["kind"] == "credential" and it.get("flag") != "deleted"}
    def resolve(name):
        it = creds.get(name)
        if not it:
            return None
        ve = (it["data"] or {}).get("value_encrypted")
        return decrypt(ve) if ve else None
    return resolve


async def apply_config(config_path, store, reloader, *, decrypt, models_client=None, hybrid=False):
    applied = await store.applied()
    staged = await store.staged()
    eff = effective(applied, staged)

    if not hybrid:
        # ---- existing non-hybrid flow (unchanged) ----
        cfg = render_config(eff, decrypt)
        try:
            validate_config(cfg)
        except ConfigError as e:
            raise ApplyError(f"invalid config, not applied: {e}") from e
        text = yaml.safe_dump(cfg, sort_keys=False)
        try:
            write_config_atomic(config_path, text)
        except Exception as e:
            raise ApplyError(f"write/readback failed, not applied: {e}") from e
        try:
            await store.fold()
        except Exception as e:
            raise ApplyError(f"config written to file but staging not cleared (DB error): {e}; re-Apply to finalize") from e
        expected = _expected_models(cfg)
        try:
            await reloader.reload_and_verify(expected)
            return {"applied": True, "servant": "healthy", "models": expected}
        except ReloadError as e:
            return {"applied": True, "servant": "unhealthy", "detail": str(e), "models": expected}

    # ---- hybrid flow ----
    settings_changed = any(s["kind"] in _RESTART_KINDS for s in staged)
    creds_changed = {s["name"] for s in staged if s["kind"] == "credential"}
    changed_ids = {s["name"] for s in staged if s["kind"] == "model" and s.get("flag") in ("new", "changed")}

    if settings_changed:                     # pre-commit: settings-only config
        cfg = render_config(eff, decrypt, hybrid=True)
        try:
            validate_config(cfg)
        except ConfigError as e:
            raise ApplyError(f"invalid config, not applied: {e}") from e
        try:
            write_config_atomic(config_path, yaml.safe_dump(cfg, sort_keys=False))
        except Exception as e:
            raise ApplyError(f"write/readback failed, not applied: {e}") from e

    try:                                     # commit
        await store.fold()
    except Exception as e:
        raise ApplyError(f"config written to file but staging not cleared (DB error): {e}; re-Apply to finalize") from e

    # post-commit (reported, not rolled back)
    resolve_key = _make_resolve_key(eff, decrypt)
    desired_items = [it for it in eff if it["kind"] == "model" and it.get("flag") != "deleted"]
    live = await models_client.list_models()
    model_report = await reconcile_models(desired_items, live, models_client,
                                          changed_ids, creds_changed, resolve_key)
    out = {"applied": True, "hybrid": True, "models": model_report}
    if settings_changed:
        expected = [it["name"] for it in desired_items]
        try:
            await reloader.reload_and_verify(expected)
            out["restart"] = "healthy"
        except ReloadError as e:
            out["restart"] = "unhealthy"; out["detail"] = str(e)
    else:
        out["restart"] = "skipped"
    return out
```

(Leave `_expected_models` and `pending_status` as they are.)

- [ ] **Step 4: Run the full engine suite**

Run: `cd ui && python -m pytest tests/test_config_engine.py -v`
Expected: PASS — the two new hybrid tests pass AND all pre-existing non-hybrid tests (`test_apply_commits_then_folds_then_restarts`, `…servant_unhealthy…`, `…validate_error…`, `…write_failure…`, `…fold_failure…`) stay green.

- [ ] **Step 5: Commit**

```bash
git add ui/app/config_engine.py ui/tests/test_config_engine.py
git commit -m "feat(engine): hybrid apply — split-render (models hot, settings restart)"
```

---

### Task 6: Wire hybrid into the Apply route + compose + apply-banner copy

**Files:**
- Modify: `ui/app/settings.py:19` (add `store_model_in_db`)
- Modify: `ui/app/routes/config_v3_routes.py:22-25,81-89` (construct `ModelsClient`, pass `hybrid`)
- Modify: `docker-compose.yml` (litellm `:63`, ui env block `:110`), `.env.example`
- Modify: `ui/frontend/src/routes/Models.svelte:186`, `Settings.svelte:64` (apply-banner copy)
- Test: `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Consumes: `apply_config(..., models_client=, hybrid=)` (Task 5), `ModelsClient` (Task 3).
- Produces: the `/api/apply` route picks the hybrid path from `settings.store_model_in_db`.

- [ ] **Step 1: Add the setting**

In `ui/app/settings.py`, after `database_url` (line 19) add:

```python
    store_model_in_db: bool = False   # mirrors the litellm container's STORE_MODEL_IN_DB; true → hybrid hot-apply
```

- [ ] **Step 2: Write a failing route test (hybrid path is chosen)**

Add to `ui/tests/test_config_v3_routes.py` a test that monkeypatches `apply_config` to capture kwargs and sets `store_model_in_db=True` via env. Follow the file's existing app-construction pattern; the assertion:

```python
def test_apply_uses_hybrid_when_store_model_in_db(monkeypatch):
    import app.routes.config_v3_routes as r
    captured = {}
    async def fake_apply(config_path, store, reloader, *, decrypt, models_client=None, hybrid=False):
        captured["hybrid"] = hybrid; captured["has_client"] = models_client is not None
        return {"applied": True, "hybrid": hybrid, "models": {"added": 0}, "restart": "skipped"}
    monkeypatch.setattr(r, "apply_config", fake_apply)
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    monkeypatch.setenv("SESSION_SECRET", "x")
    # … construct the TestClient with an authenticated session as the other tests in this file do …
    # resp = client.post("/api/apply"); assert resp.status_code == 200
    assert captured["hybrid"] is True and captured["has_client"] is True
```

(Match the exact TestClient/login setup already used in `test_config_v3_routes.py`; reuse its fixtures.)

- [ ] **Step 3: Run to verify it fails**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -k hybrid -v`
Expected: FAIL — route still calls `apply_config` without `models_client`/`hybrid`.

- [ ] **Step 4: Construct `ModelsClient` + pass hybrid in the route**

In `ui/app/routes/config_v3_routes.py`, add an import and a factory, and update `apply()`:

```python
from app.models_client import ModelsClient

def make_models_client() -> ModelsClient:
    s = get_settings()
    return ModelsClient(s.litellm_base_url, s.litellm_master_key)

@router.post("/apply", dependencies=[Depends(login_required)])
async def apply():
    s = get_settings(); f = _fernet()
    try:
        return await apply_config(
            s.config_path, make_config_store(), make_reloader(),
            decrypt=lambda b: f.decrypt(b.encode()).decode(),
            models_client=make_models_client() if s.store_model_in_db else None,
            hybrid=s.store_model_in_db,
        )
    except ApplyError as e:
        code = 422 if "invalid" in str(e) else 500
        raise HTTPException(status_code=code, detail=str(e))
```

- [ ] **Step 5: Run route + full backend suite**

Run: `cd ui && python -m pytest tests/ -q`
Expected: PASS — new route test passes; nothing else regresses.

- [ ] **Step 6: Wire `STORE_MODEL_IN_DB` into compose for BOTH containers**

In `docker-compose.yml`, change the litellm env (line 63) and add the same to the ui env block (around line 110), both sourced from `.env`:

```yaml
      STORE_MODEL_IN_DB: "${STORE_MODEL_IN_DB:-false}"
```

Add to `.env.example`:

```
# Hybrid hot-apply: when true, models apply live via the LiteLLM /model API (no restart).
# Both the litellm and ui containers read this; flip together and recreate the stack.
STORE_MODEL_IN_DB=false
```

- [ ] **Step 7: Make the apply banner honest about hot vs restart**

`Models.svelte:186` currently always says "Applying… restarting the proxy (~25s)". Replace with copy that doesn't promise a restart (models are hot in hybrid):

```svelte
  {#if store.applying}<div class="banner info">Applying changes…</div>{/if}
```

Apply the same softened copy to `Settings.svelte:64` (the passthrough card still restarts, so keep its specific note there is fine — but a generic "Applying…" is acceptable; choose one and keep it consistent).

- [ ] **Step 8: Build + commit**

Run: `cd ui/frontend && npm run build` (expect success).

```bash
git add ui/app/settings.py ui/app/routes/config_v3_routes.py ui/tests/test_config_v3_routes.py docker-compose.yml .env.example ui/frontend/src/routes/Models.svelte ui/frontend/src/routes/Settings.svelte
git commit -m "feat: route Apply through hybrid when STORE_MODEL_IN_DB; wire compose env; honest apply banner"
```

---

### Task 7: Migration — `prepare-hot-apply` + Settings runbook (empty-then-fill)

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py` (add `POST /api/config/prepare-hot-apply`)
- Modify: `ui/frontend/src/lib/api.js` (add `prepareHotApply`)
- Modify: `ui/frontend/src/routes/Settings.svelte` (add an "Enable hot-apply" card with the runbook)
- Test: `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Produces: `POST /api/config/prepare-hot-apply` — renders the settings-only (`hybrid=True`) config.yaml from current effective state and writes it (emptying `model_list`), then restarts litellm so it comes up with **zero models** while still non-hybrid. Returns `{"prepared": True, "next": "<runbook>"}`. This guarantees empty-then-fill: config has no models before the operator flips `STORE_MODEL_IN_DB=true` and recreates; the first hybrid Apply then fills the empty DB.
- Consumes: `render_config(..., hybrid=True)`, `validate_config`, `write_config_atomic`, `make_reloader()` (all existing).

- [ ] **Step 1: Write a failing test**

Add to `ui/tests/test_config_v3_routes.py` (reusing the file's app/login fixtures):

```python
def test_prepare_hot_apply_writes_empty_model_list(...):
    # POST /api/config/prepare-hot-apply with a stack that has a model staged/applied
    # assert 200, response["prepared"] is True
    # assert the written config.yaml has model_list == [] and no credential_list
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -k prepare -v`
Expected: FAIL — route not found (404).

- [ ] **Step 3: Implement the route**

In `ui/app/routes/config_v3_routes.py`:

**Task-1 finding folded in:** LiteLLM's env `STORE_MODEL_IN_DB=true` overrides `general_settings.store_model_in_db` (verified: `/model/new` worked with env=true while config said false). For reproducibility (the export is the master), `ui_config` must agree — so the migration stages `general_setting store_model_in_db=true`, which then renders into `config.yaml` and is folded by the post-recreate Apply.

```python
@router.post("/config/prepare-hot-apply", dependencies=[Depends(login_required)])
async def prepare_hot_apply():
    s = get_settings(); f = _fernet()
    store = make_config_store()
    # Make ui_config (the master) agree with the STORE_MODEL_IN_DB=true env, so the
    # rendered config + export are reproducible — not just the runtime env. Staged
    # here; folded by the post-recreate Apply.
    await store.stage('general_setting', 'store_model_in_db', True)
    eff = effective(await store.applied(), await store.staged())
    cfg = render_config(eff, decrypt=lambda b: f.decrypt(b.encode()).decode(), hybrid=True)
    try:
        validate_config(cfg)
        write_config_atomic(s.config_path, _yaml.safe_dump(cfg, sort_keys=False))
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=f"invalid config: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    try:
        await make_reloader().reload_and_verify([])   # comes up with zero models
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"proxy did not restart cleanly: {e}")
    return {"prepared": True,
            "next": "config.yaml now has no models. Set STORE_MODEL_IN_DB=true in .env, run "
                    "`docker compose up -d` to recreate the stack, then click Apply to fill the model DB."}
```

(Ensure `validate_config`, `ConfigError`, `render_config`, `effective` are imported at the top — `effective`/`render_config` already are; add `from app.config_store import validate_config, ConfigError` — `ConfigError` is already imported, add `validate_config`.)

- [ ] **Step 4: Add the api method**

In `ui/frontend/src/lib/api.js`, add to the `api` object:

```js
  prepareHotApply: () => req('/api/config/prepare-hot-apply', { method: 'POST' }),
```

- [ ] **Step 5: Add the Settings "Enable hot-apply" card**

In `ui/frontend/src/routes/Settings.svelte`, add a card (script state + markup):

```js
  let hotBusy = $state(false), hotMsg = $state(''), hotErr = $state('')
  async function prepareHotApply() {
    hotBusy = true; hotMsg = ''; hotErr = ''
    try { const r = await api.prepareHotApply(); hotMsg = r.next } catch (e) { hotErr = e.message }
    finally { hotBusy = false }
  }
```

```svelte
  <div class="card"><h2>Enable hot-apply (model changes without restart)</h2>
    <p class="hint">One-time migration. Step 1 empties the model list from config.yaml and restarts the proxy (brief downtime). Then set <code>STORE_MODEL_IN_DB=true</code> in <code>.env</code>, run <code>docker compose up -d</code>, and click Apply to fill the model DB. After this, model add/edit/delete apply instantly.</p>
    <div class="row"><button onclick={prepareHotApply} disabled={hotBusy}>{hotBusy ? 'Preparing…' : 'Step 1: Prepare (empty config models + restart)'}</button></div>
    {#if hotErr}<div class="banner err">{hotErr}</div>{/if}
    {#if hotMsg}<div class="banner ok">{hotMsg}</div>{/if}
  </div>
```

- [ ] **Step 6: Run backend tests + build**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -v` (expect PASS) and `cd ui/frontend && npm run build` (expect success).

- [ ] **Step 7: Commit**

```bash
git add ui/app/routes/config_v3_routes.py ui/frontend/src/lib/api.js ui/frontend/src/routes/Settings.svelte ui/tests/test_config_v3_routes.py
git commit -m "feat: prepare-hot-apply migration (empty-then-fill) + Settings runbook"
```

---

### Task 8: `ui_config` export endpoint (the new reproducibility artifact)

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py` (add `GET /api/config/export`)
- Modify: `ui/frontend/src/routes/Settings.svelte:66-71` (retarget the existing export link/label)
- Test: `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Produces: `GET /api/config/export` → JSON attachment (`ui_config.json`): `{"version": 1, "items": [{kind,name,data}…]}` from `applied`. Credentials are exported with `value_encrypted` intact (Fernet-encrypted) — **never plaintext**.
- Consumes: `ConfigStore.applied()` (existing). Note `api.js` already declares `exportConfigUrl: '/api/config/export'` and `Settings.svelte` links to it — the route is currently missing (404); this implements it.

- [ ] **Step 1: Write a failing test**

Add to `ui/tests/test_config_v3_routes.py`:

```python
def test_export_returns_items_with_encrypted_credentials(...):
    # seed applied with a model + a credential (value_encrypted="ENC")
    # GET /api/config/export → 200, JSON has version==1
    # the credential item's data.value_encrypted == "ENC" (encrypted, present)
    # assert NO plaintext api_key field appears anywhere in the payload
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -k export -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implement the route**

In `ui/app/routes/config_v3_routes.py`:

```python
from fastapi.responses import JSONResponse

@router.get("/config/export", dependencies=[Depends(login_required)])
async def export_config():
    store = make_config_store()
    items = await store.applied()   # [{kind,name,data}] — credentials carry value_encrypted, never plaintext
    payload = {"version": 1, "items": items}
    return JSONResponse(payload, headers={"Content-Disposition": "attachment; filename=ui_config.json"})
```

- [ ] **Step 4: Retarget the Settings export card**

In `ui/frontend/src/routes/Settings.svelte` (lines 66-71), update the card to reflect the new artifact:

```svelte
  <div class="card"><h2>Export config (ui_config)</h2>
    <p class="hint">Download a snapshot of the UI's source-of-truth config (models, settings, encrypted credentials). This is the reproducibility/backup artifact — restore it on a fresh stack. Credentials are exported encrypted (restoreable only with the same SESSION_SECRET).</p>
    <div class="row">
      <a class="btn" href={api.exportConfigUrl} download>⬇ Export ui_config.json</a>
    </div>
  </div>
```

- [ ] **Step 5: Run backend tests + build**

Run: `cd ui && python -m pytest tests/test_config_v3_routes.py -v` (PASS) and `cd ui/frontend && npm run build` (success).

- [ ] **Step 6: Commit**

```bash
git add ui/app/routes/config_v3_routes.py ui/frontend/src/routes/Settings.svelte ui/tests/test_config_v3_routes.py
git commit -m "feat: GET /api/config/export (ui_config.json, encrypted creds) + retarget Settings link"
```

---

### Task 9: End-to-end integration + release prep

**Files:** none modified — verification + handoff.

- [ ] **Step 1: Full backend suite green**

Run: `cd ui && python -m pytest tests/ -q`
Expected: all pass (existing + Tasks 2–8 additions).

- [ ] **Step 2: Production build**

Run: `cd ui/frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Migrate a real local stack (empty-then-fill)**

On the local stack (with a couple of models configured, non-hybrid): Settings → "Step 1: Prepare" → confirm config.yaml `model_list: []` and the proxy restarts with zero models. Set `STORE_MODEL_IN_DB=true` in `.env`, `docker compose up -d`. Click Apply → confirm `/v1/models` now lists exactly the UI's models (filled into the empty DB), with no duplication.

- [ ] **Step 4: Prove hot vs restart on the LAN IP (Playwright)**

On `http://10.0.20.85:8081` (hybrid now active):
- **Model hot:** add a new model → Apply → assert it appears in `/api/models/health` (or `/v1/models`) and the litellm container's uptime did **not** reset (`docker inspect -f '{{.State.StartedAt}}'` unchanged) — i.e. no restart.
- **Settings restart:** change a router setting → Apply → assert the container restarted (StartedAt advanced) and the proxy is healthy, and the previously-added model **survived** (DB-persisted).
- **Credential rotation hot:** edit a credential's value → Apply → assert the referencing models were re-pushed (Apply response `models.updated ≥ 1`) with no restart.
- **Delete:** delete a model → Apply → gone from `/v1/models`, no restart.

- [ ] **Step 5: Export round-trips**

Click "Export ui_config.json" → file downloads, contains models + settings, credentials only as `value_encrypted` (grep the file for any plaintext key prefix → none).

- [ ] **Step 6: Hand off**

Report: branch ready, all backend tests green, build clean, integration verified (hot add/no-restart, settings restart + survival, cred rotation hot, export encrypted). Record `1.21.0` notes. The human merges → semantic-release cuts `1.21.0` + the image; the human flips `STORE_MODEL_IN_DB=true` on the live stack via the runbook and bumps the compose pin.

---

## Self-Review

**Spec coverage:**
- §2 keystone (STORE_MODEL_IN_DB, settings-only secret-free config) → Tasks 2 (render), 6 (compose/setting). ✓
- §3.1 boundary / §3.2 predicate (settings vs creds vs models) → Task 5. ✓
- §3.3 reconcile (declarative diff, inline key, forced-update on rotation, missing-cred reported) → Task 4. ✓
- §3.5 empty-then-fill migration → Task 7. ✓
- §5 credential inline-resolve lifecycle → Tasks 2 (`render_model_entry`), 4 (`reconcile_models`), 5 (`creds_changed` → force update). ✓
- §6 export/import (export endpoint; import already exists) → Task 8. ✓
- §7 core-behavior integration assertions → Task 1 + Task 9. ✓
- §11 testing (render hybrid, reconcile diff incl. forced-update + missing-cred, apply split, export encrypted, Playwright hot/restart) → Tasks 2,4,5,8,9. ✓
- §10 per-key routing nudge — **deferred** (near-zero copy change; fold into the next docs pass, not gating). Noted here so it isn't silently dropped.

**Placeholder scan:** Backend steps carry full code. Three route tests (Task 6 step 2, Task 7 step 1, Task 8 step 1) describe assertions rather than full TestClient boilerplate **because** they must reuse `test_config_v3_routes.py`'s existing app/login fixtures verbatim — the implementer copies that file's setup. Flagged explicitly, not a silent gap.

**Type/name consistency:** `render_model_entry(it, resolve_key)` signature identical across Tasks 2/4. `diff_models(desired, live, changed_ids, force_ids)` and `reconcile_models(desired_items, live, client, changed_ids, force_ids, resolve_key)` identical across Tasks 4/5. `apply_config(..., models_client=, hybrid=)` identical across Tasks 5/6. `ModelsClient` methods (`list_models/add_model/update_model/delete_model`) identical across Tasks 3/4/5. `store_model_in_db` setting consistent Tasks 6/7. `exportConfigUrl` matches the existing `api.js`.
