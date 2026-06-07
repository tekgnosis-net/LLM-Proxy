# LLM-Proxy Admin UI — v3.1: Config Engine + Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD. Pure functions get unit tests; the asyncpg store is integration-verified (like `catalog`/`db_admin`). Steps use `- [ ]`. **Branch: `v3-master-servant`.**

**Goal:** Build the Master/Servant config engine — the DB-authoritative staged/applied model, the pure render, the commit-at-write Apply, Discard, pending, and bootstrap-import — as a backend library verified without the UI. (HTTP API = v3.2; frontend = v3.3.)

**Architecture:** Two Postgres tables (`ui_config_applied`, `ui_config_staged`) keyed by `(kind, name)`. Pure functions compute the *effective* view (applied ⊕ staged with flags) and *render* it to a `config.yaml` dict. The engine orchestrates save/discard/apply with the commit boundary at a successful read-back file write (no post-write rollback). Bootstrap imports an existing `config.yaml` into items.

**Tech Stack:** FastAPI app context, asyncpg, `cryptography` (Fernet, from v2.2), PyYAML. No new deps.

**Spec:** [`../specs/2026-06-08-llm-proxy-ui-v3-master-servant-config.md`](../specs/2026-06-08-llm-proxy-ui-v3-master-servant-config.md).

---

## File Structure
```
ui/app/config_render.py   # CREATE: pure — effective(), render_config(), kind↔section mapping, redact()
ui/app/config_db.py       # CREATE: ConfigStore (asyncpg) — schema, CRUD, effective rows, fold, clear
ui/app/config_import.py   # CREATE: pure split of a config.yaml dict → items (+ passthrough, encrypted creds)
ui/app/config_engine.py   # CREATE: orchestration — save_item, discard, apply (commit boundary), pending
ui/app/config_store.py    # REUSE: validate_config, atomic write, ProxyConfig, restore/backup helpers
ui/app/credentials_store.py # REUSE: fernet_from_secret
ui/app/reloader.py        # REUSE: Reloader.reload_and_verify
ui/tests/test_config_render.py   # CREATE (pure)
ui/tests/test_config_import.py   # CREATE (pure)
ui/tests/test_config_engine.py   # CREATE (fakes for db/reloader)
```

Item shape (a plain dict): `{"kind": str, "name": str, "data": <json>, "flag": "new"|"changed"|"deleted"|None}`.
Kinds + name: `model`→model_name (`data={litellm_params, model_info}`); `credential`→credential_name (`data={provider, value_encrypted}`); `router_setting`/`litellm_setting`/`general_setting`→the key (`data`=value); `passthrough`→`"_"` (`data`=raw dict).

---

## Task 1: config_render — effective() (TDD, pure)

**Files:** Create `ui/app/config_render.py`, `ui/tests/test_config_render.py`.

- [ ] **Step 1: failing test:**
```python
from app.config_render import effective

def test_effective_overlays_staged_flags():
    applied = [
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o"}}},
        {"kind": "router_setting", "name": "routing_strategy", "data": "simple-shuffle"},
    ]
    staged = [
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": "changed"},
        {"kind": "model", "name": "claude", "data": {"litellm_params": {"model": "anthropic/claude-3"}}, "flag": "new"},
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o"}}, "flag": "deleted"},
    ]
    eff = {(i["kind"], i["name"]): i for i in effective(applied, staged)}
    assert eff[("router_setting", "routing_strategy")]["data"] == "least-busy"
    assert eff[("router_setting", "routing_strategy")]["flag"] == "changed"
    assert eff[("model", "claude")]["flag"] == "new"
    assert eff[("model", "gpt")]["flag"] == "deleted"            # kept, marked
    assert eff[("model", "gpt")]["data"]["litellm_params"]["model"] == "openai/gpt-4o"
```

- [ ] **Step 2: run red** — `cd ui && .venv/bin/python -m pytest tests/test_config_render.py -k effective -v` → FAIL.

- [ ] **Step 3: implement** in `config_render.py`:
```python
from __future__ import annotations
from typing import Any, Callable, Optional

def effective(applied: list[dict], staged: list[dict]) -> list[dict]:
    """Overlay staged onto applied. Returns items with a `flag` (None=clean,
    'new'/'changed'/'deleted'). Deleted items are KEPT (marked) so the UI can strike them."""
    out: dict[tuple, dict] = {}
    for it in applied:
        out[(it["kind"], it["name"])] = {**it, "flag": None}
    for st in staged:
        key = (st["kind"], st["name"])
        flag = st.get("flag")
        if flag == "deleted":
            base = out.get(key, {"kind": st["kind"], "name": st["name"], "data": st.get("data")})
            out[key] = {**base, "flag": "deleted"}
        else:  # new | changed
            out[key] = {"kind": st["kind"], "name": st["name"], "data": st["data"], "flag": flag}
    return list(out.values())
```

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): config_render.effective (applied ⊕ staged with flags)`.

---

## Task 2: config_render — render_config() + redact() (TDD, pure)

**Files:** Modify `ui/app/config_render.py`, `ui/tests/test_config_render.py`.

- [ ] **Step 1: failing tests:**
```python
from app.config_render import render_config, redact_rendered

def test_render_groups_items_into_sections_and_decrypts_creds():
    items = [
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "openai"}, "model_info": {"mode": "chat"}}, "flag": None},
        {"kind": "credential", "name": "openai", "data": {"provider": "openai", "value_encrypted": "ENC"}, "flag": None},
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": None},
        {"kind": "litellm_setting", "name": "cache", "data": True, "flag": None},
        {"kind": "general_setting", "name": "store_model_in_db", "data": False, "flag": None},
        {"kind": "model", "name": "old", "data": {"litellm_params": {"model": "x/y"}}, "flag": "deleted"},
        {"kind": "passthrough", "name": "_", "data": {"litellm_settings": {"drop_params": True}}, "flag": None},
    ]
    cfg = render_config(items, decrypt=lambda b: "sk-REAL")
    assert cfg["router_settings"] == {"routing_strategy": "least-busy"}
    assert cfg["general_settings"] == {"store_model_in_db": False}
    assert cfg["litellm_settings"]["cache"] is True
    assert cfg["litellm_settings"]["drop_params"] is True          # passthrough deep-merged
    assert {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "openai"}, "model_info": {"mode": "chat"}} in cfg["model_list"]
    assert all(m["model_name"] != "old" for m in cfg["model_list"])  # deleted excluded
    assert cfg["credential_list"][0] == {"credential_name": "openai", "credential_values": {"api_key": "sk-REAL"}, "credential_info": {"provider": "openai"}}

def test_render_managed_wins_over_passthrough():
    items = [
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": None},
        {"kind": "passthrough", "name": "_", "data": {"router_settings": {"routing_strategy": "EVIL", "extra": 1}}, "flag": None},
    ]
    cfg = render_config(items, decrypt=lambda b: "")
    assert cfg["router_settings"]["routing_strategy"] == "least-busy"   # managed wins
    assert cfg["router_settings"]["extra"] == 1                          # passthrough extra key kept

def test_redact_masks_credential_values():
    cfg = {"credential_list": [{"credential_name": "x", "credential_values": {"api_key": "sk-REAL"}}]}
    assert redact_rendered(cfg)["credential_list"][0]["credential_values"]["api_key"] == "***"
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement** in `config_render.py`:
```python
import copy

_SECTION_BY_KIND = {"router_setting": "router_settings", "litellm_setting": "litellm_settings",
                    "general_setting": "general_settings"}

def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out

def render_config(items: list[dict], decrypt: Callable[[Any], str]) -> dict:
    """Pure: assemble a config.yaml dict from effective items. The kind='passthrough'
    item (if any) is the lowest-precedence base; managed sections override it. `decrypt`
    turns a credential's value_encrypted into the plaintext api_key. Deleted items excluded."""
    base: dict = {}
    model_list, credential_list = [], []
    sections: dict[str, dict] = {}
    for it in items:
        if it.get("flag") == "deleted":
            continue
        kind, name, data = it["kind"], it["name"], it["data"]
        if kind == "passthrough":
            base = copy.deepcopy(data) if isinstance(data, dict) else {}
        elif kind == "model":
            model_list.append({"model_name": name, **data})
        elif kind == "credential":
            credential_list.append({"credential_name": name,
                                    "credential_values": {"api_key": decrypt(data.get("value_encrypted"))},
                                    "credential_info": {"provider": data.get("provider")}})
        elif kind in _SECTION_BY_KIND:
            sections.setdefault(_SECTION_BY_KIND[kind], {})[name] = data
    cfg = base
    for sec, kv in sections.items():
        cfg[sec] = _deep_merge(base.get(sec, {}), kv) if isinstance(base.get(sec), dict) else dict(kv)
    if model_list: cfg["model_list"] = model_list
    else: cfg.setdefault("model_list", [])
    if credential_list: cfg["credential_list"] = credential_list
    return cfg

def redact_rendered(cfg: dict) -> dict:
    cl = cfg.get("credential_list")
    if isinstance(cl, list):
        cfg = {**cfg, "credential_list": [{**c, "credential_values": {k: "***" for k in (c.get("credential_values") or {})}} for c in cl]}
    return cfg
```

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): config_render.render_config + redact (items → config.yaml dict)`.

---

## Task 3: config_import — config.yaml dict → items (TDD, pure)

**Files:** Create `ui/app/config_import.py`, `ui/tests/test_config_import.py`.

- [ ] **Step 1: failing tests:**
```python
from app.config_import import split_config

def test_split_known_sections_to_items_and_passthrough():
    cfg = {
        "model_list": [{"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {"mode": "chat"}}],
        "router_settings": {"routing_strategy": "least-busy", "num_retries": 2},
        "litellm_settings": {"cache": True},
        "general_settings": {"store_model_in_db": False, "master_key": "os.environ/LITELLM_MASTER_KEY"},
        "credential_list": [{"credential_name": "openai", "credential_values": {"api_key": "sk-REAL"}, "credential_info": {"provider": "openai"}}],
        "callbacks": ["langfuse"],            # unknown → passthrough
        "environment_variables": {"X": "1"},  # unknown → passthrough
    }
    items, passthrough = split_config(cfg, encrypt=lambda s: f"ENC({s})")
    by = {(i["kind"], i["name"]): i for i in items}
    assert by[("model", "gpt")]["data"]["litellm_params"]["model"] == "openai/gpt-4o"
    assert by[("router_setting", "routing_strategy")]["data"] == "least-busy"
    assert by[("router_setting", "num_retries")]["data"] == 2
    assert by[("litellm_setting", "cache")]["data"] is True
    assert by[("general_setting", "store_model_in_db")]["data"] is False
    assert by[("credential", "openai")]["data"] == {"provider": "openai", "value_encrypted": "ENC(sk-REAL)"}
    assert passthrough == {"callbacks": ["langfuse"], "environment_variables": {"X": "1"}}

def test_split_empty_config():
    items, passthrough = split_config({}, encrypt=lambda s: s)
    assert items == [] and passthrough == {}
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement `config_import.py`:**
```python
from __future__ import annotations
from typing import Callable

_KNOWN = {"model_list", "router_settings", "litellm_settings", "general_settings", "credential_list"}
_DICT_SECTION_KIND = {"router_settings": "router_setting", "litellm_settings": "litellm_setting",
                      "general_settings": "general_setting"}

def split_config(cfg: dict, encrypt: Callable[[str], str]) -> tuple[list[dict], dict]:
    """Pure: split a config.yaml dict into typed items + a passthrough dict (unknown keys).
    Credential api_keys are encrypted via `encrypt`."""
    items: list[dict] = []
    for m in (cfg.get("model_list") or []):
        name = m.get("model_name")
        data = {k: v for k, v in m.items() if k != "model_name"}
        items.append({"kind": "model", "name": name, "data": data})
    for sec, kind in _DICT_SECTION_KIND.items():
        for key, val in (cfg.get(sec) or {}).items():
            items.append({"kind": kind, "name": key, "data": val})
    for c in (cfg.get("credential_list") or []):
        api_key = (c.get("credential_values") or {}).get("api_key", "")
        provider = (c.get("credential_info") or {}).get("provider")
        items.append({"kind": "credential", "name": c.get("credential_name"),
                      "data": {"provider": provider, "value_encrypted": encrypt(api_key)}})
    passthrough = {k: v for k, v in cfg.items() if k not in _KNOWN}
    return items, passthrough
```

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): config_import.split_config (config.yaml → items + passthrough)`.

---

## Task 4: config_db — applied/staged store (TDD-light + integration)

**Files:** Create `ui/app/config_db.py`. Unit-test the staged-flag decision (pure helper); the asyncpg CRUD is integration-verified in Task 6.

- [ ] **Step 1: failing test** (`tests/test_config_engine.py`, the pure flag helper):
```python
from app.config_db import decide_flag

def test_decide_flag():
    assert decide_flag(applied_has=False) == "new"
    assert decide_flag(applied_has=True) == "changed"
```

- [ ] **Step 2: run red** → FAIL. **Step 3: implement `config_db.py`:**
```python
from __future__ import annotations
import json
import asyncpg
from typing import Optional

APPLIED, STAGED = "ui_config_applied", "ui_config_staged"

def decide_flag(applied_has: bool) -> str:
    return "changed" if applied_has else "new"

class ConfigStore:
    def __init__(self, dsn: str): self._dsn = dsn
    async def _conn(self): return await asyncpg.connect(self._dsn)

    async def ensure_schema(self, conn) -> None:
        await conn.execute(f'''CREATE TABLE IF NOT EXISTS {APPLIED} (
            kind text NOT NULL, name text NOT NULL, data jsonb NOT NULL,
            updated_at timestamptz DEFAULT now(), PRIMARY KEY(kind, name))''')
        await conn.execute(f'''CREATE TABLE IF NOT EXISTS {STAGED} (
            kind text NOT NULL, name text NOT NULL, data jsonb NOT NULL, flag text NOT NULL,
            updated_at timestamptz DEFAULT now(), PRIMARY KEY(kind, name))''')

    @staticmethod
    def _rows(recs): return [{"kind": r["kind"], "name": r["name"], "data": json.loads(r["data"]),
                              **({"flag": r["flag"]} if "flag" in r else {})} for r in recs]

    async def applied(self) -> list[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return self._rows(await conn.fetch(f"SELECT kind,name,data FROM {APPLIED} ORDER BY kind,name"))
        finally: await conn.close()

    async def staged(self) -> list[dict]:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return self._rows(await conn.fetch(f"SELECT kind,name,data,flag FROM {STAGED} ORDER BY kind,name"))
        finally: await conn.close()

    async def stage(self, kind: str, name: str, data, *, deleted: bool = False) -> None:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            if deleted:
                flag = "deleted"
            else:
                has = await conn.fetchval(f"SELECT 1 FROM {APPLIED} WHERE kind=$1 AND name=$2", kind, name)
                flag = decide_flag(bool(has))
            await conn.execute(f'''INSERT INTO {STAGED}(kind,name,data,flag) VALUES($1,$2,$3,$4)
                ON CONFLICT(kind,name) DO UPDATE SET data=$3, flag=$4, updated_at=now()''',
                kind, name, json.dumps(data), flag)
        finally: await conn.close()

    async def clear_staged(self, kind: Optional[str] = None, name: Optional[str] = None) -> None:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            if kind and name: await conn.execute(f"DELETE FROM {STAGED} WHERE kind=$1 AND name=$2", kind, name)
            else: await conn.execute(f"DELETE FROM {STAGED}")
        finally: await conn.close()

    async def staged_count(self) -> int:
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            return int(await conn.fetchval(f"SELECT count(*) FROM {STAGED}"))
        finally: await conn.close()

    async def fold(self) -> None:
        """Apply staged into applied, then clear staged. Call only after a committed file write."""
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            async with conn.transaction():
                await conn.execute(f"DELETE FROM {APPLIED} a USING {STAGED} s WHERE a.kind=s.kind AND a.name=s.name AND s.flag='deleted'")
                await conn.execute(f'''INSERT INTO {APPLIED}(kind,name,data,updated_at)
                    SELECT kind,name,data,now() FROM {STAGED} WHERE flag IN ('new','changed')
                    ON CONFLICT(kind,name) DO UPDATE SET data=EXCLUDED.data, updated_at=now()''')
                await conn.execute(f"DELETE FROM {STAGED}")
        finally: await conn.close()

    async def seed_applied(self, items: list[dict]) -> None:
        """Bootstrap: populate applied from imported items (only if empty)."""
        conn = await self._conn()
        try:
            await self.ensure_schema(conn)
            if await conn.fetchval(f"SELECT 1 FROM {APPLIED} LIMIT 1"): return
            async with conn.transaction():
                for it in items:
                    await conn.execute(f"INSERT INTO {APPLIED}(kind,name,data) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                                       it["kind"], it["name"], json.dumps(it["data"]))
        finally: await conn.close()
```

- [ ] **Step 4: green (the pure test) + full suite.** **Step 5: commit** `feat(ui): config_db ConfigStore (applied/staged tables, stage/fold/clear)`.

---

## Task 5: config_engine — save_item / discard / apply (TDD with fakes)

**Files:** Create `ui/app/config_engine.py`, extend `ui/tests/test_config_engine.py`.

- [ ] **Step 1: failing tests** (fakes for the store + reloader + a passthrough getter):
```python
import pytest
from app.config_engine import apply_config, ApplyError

class FakeStore:
    def __init__(self):
        self._applied=[{"kind":"router_setting","name":"routing_strategy","data":"simple-shuffle"}]
        self._staged=[{"kind":"router_setting","name":"routing_strategy","data":"least-busy","flag":"changed"}]
        self.folded=False
    async def applied(self): return list(self._applied)
    async def staged(self): return list(self._staged)
    async def staged_count(self): return len(self._staged)
    async def fold(self): self.folded=True; self._staged=[]

class FakeReloader:
    def __init__(self, ok=True): self.ok=ok; self.calls=0
    async def reload_and_verify(self, expected):
        self.calls+=1
        if not self.ok:
            from app.reloader import ReloadError; raise ReloadError("sim")
        return True

@pytest.mark.asyncio
async def test_apply_commits_then_folds_then_restarts(tmp_path):
    p=str(tmp_path/"config.yaml"); store=FakeStore(); rl=FakeReloader(ok=True)
    res=await apply_config(p, store, rl, decrypt=lambda b:"", write_fn=_write_and_readback(p))
    assert res["applied"] is True and res["servant"]=="healthy"
    assert store.folded is True
    import yaml; assert yaml.safe_load(open(p))["router_settings"]["routing_strategy"]=="least-busy"

@pytest.mark.asyncio
async def test_apply_servant_unhealthy_still_committed(tmp_path):
    p=str(tmp_path/"config.yaml"); store=FakeStore(); rl=FakeReloader(ok=False)
    res=await apply_config(p, store, rl, decrypt=lambda b:"", write_fn=_write_and_readback(p))
    assert res["applied"] is True and res["servant"]=="unhealthy"
    assert store.folded is True   # committed despite restart failure — NO rollback
```
(Provide a small `_write_and_readback(p)` test helper that writes + reads back, or let `apply_config` use `config_store.write_config`. Prefer: `apply_config` does the write+readback internally via `config_store` atomic write; the test asserts the file content. Adjust the test to call `apply_config(p, store, rl, decrypt=...)` without injecting write_fn if you implement the write inside.)

- [ ] **Step 2: run red** → FAIL. **Step 3: implement `config_engine.py`:**
```python
from __future__ import annotations
import yaml
from pathlib import Path
from app.config_render import effective, render_config
from app.config_store import validate_config, ConfigError, write_config_atomic  # see note
from app.reloader import ReloadError

class ApplyError(RuntimeError): pass

def _expected_models(cfg: dict) -> list[str]:
    return [m.get("model_name") for m in (cfg.get("model_list") or [])]

async def apply_config(config_path, store, reloader, *, decrypt) -> dict:
    # 1. render effective
    eff = effective(await store.applied(), await store.staged())
    cfg = render_config(eff, decrypt)   # passthrough is a kind='passthrough' item inside eff
    # 2. validate (pre-commit; raise ApplyError 'invalid' on failure)
    try:
        validate_config(cfg)
    except ConfigError as e:
        raise ApplyError(f"invalid config, not applied: {e}") from e
    # 3. write temp + read back + re-parse (pre-commit)
    text = yaml.safe_dump(cfg, sort_keys=False)
    try:
        write_config_atomic(config_path, text)          # backup + temp + os.replace + chmod 0600
        readback = yaml.safe_load(Path(config_path).read_text())
        assert readback is not None
    except Exception as e:
        raise ApplyError(f"write/readback failed, not applied: {e}") from e
    # 4. COMMIT: fold staged into applied + clear staged
    await store.fold()
    # 5. restart + verify (reported, NOT rolled back)
    try:
        await reloader.reload_and_verify(_expected_models(cfg))
        return {"applied": True, "servant": "healthy", "models": _expected_models(cfg)}
    except ReloadError as e:
        return {"applied": True, "servant": "unhealthy", "detail": str(e), "models": _expected_models(cfg)}

async def pending_status(store) -> dict:
    n = await store.staged_count()
    return {"pending": n > 0, "count": n}
```
**NOTE:** add a `write_config_atomic(path, text)` to `config_store.py` (extract the existing atomic write: backup `*.bak.*` 0600, temp + `os.replace`, `chmod 0600`). Tests for it can assert 0600 + backup (mirror the existing write_config tests). `validate_config(cfg)` must accept a plain dict (it does — it builds `ProxyConfig`).

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): config_engine.apply_config (commit-at-write, fold, no rollback) + pending`.

---

## Task 6: bootstrap import + real-DB integration verification

**Files:** `ui/app/main.py` (lifespan import), integration via the real stack.

- [ ] **Step 1: bootstrap** — in `main.create_app()` lifespan (when `database_url`), on startup: read the current `config.yaml` (via `config_store.load_config(...).model_dump(exclude_none=True)`), `items, passthrough = split_config(cfg, encrypt=<fernet.encrypt→str wrapper>)`, append the passthrough as an item `items.append({"kind":"passthrough","name":"_","data":passthrough})` (only if `passthrough` is non-empty), then `ConfigStore(dsn).seed_applied(items)` (which no-ops if `ui_config_applied` is already populated — idempotent). Passthrough is thus just a normal applied item; no special store method needed. Commit `feat(ui): bootstrap-import config.yaml into the config DB on first run`.

- [ ] **Step 2: real-DB integration** (local-build stack, like prior phases): bring up; exec into the UI container and drive the engine directly:
```python
# docker compose exec llm-proxy-ui python3 - <<'PY'
import asyncio, yaml
from app.config_db import ConfigStore
from app.config_engine import apply_config, pending_status
from app.settings import get_settings
from app.credentials_store import fernet_from_secret
from app.reloader import make... # or a direct Reloader
s=get_settings(); store=ConfigStore(s.database_url); f=fernet_from_secret(s.credentials_key or s.session_secret)
async def main():
    print("applied:", len(await store.applied()))          # bootstrap-seeded from config.yaml.example
    await store.stage("router_setting","routing_strategy","least-busy")
    print("pending:", await pending_status(store))          # pending True, count 1
    # apply via the engine (real reloader) — verify file rendered + folded + servant restart
asyncio.run(main())
PY
```
   Verify: bootstrap seeded applied from the example; stage → pending; apply → config.yaml rendered (routing least-busy), staged cleared, applied folded, container restarted healthy; a second apply with a bad-but-valid setting still commits (servant reported). Tear down; restore config.

- [ ] **Step 3: commit** any fixes from integration. 

## Self-Review
- **Spec coverage:** two-table model (T4) ✓; effective with flags (T1) ✓; render incl. decrypt + passthrough-merge + managed-wins + deleted-excluded (T2) ✓; redact (T2) ✓; import split incl. cred-encrypt + passthrough (T3) ✓; commit-at-write apply + fold + no-rollback + servant-reported (T5) ✓; pending (T5) ✓; bootstrap import (T6) ✓.
- **Placeholders:** Task 5 notes the `write_config_atomic` extraction explicitly + Task 6's integration snippet is a real harness (not pseudocode to leave dangling — the engineer wires the actual Reloader). The `make...` reloader line must be replaced with the real `Reloader(...)` construction during T6.
- **Type consistency:** item dict shape `{kind,name,data,flag}` consistent; passthrough is a `kind='passthrough'` item (no special store method); `render_config(items, decrypt)` (passthrough read from items); `effective`/`render_config`/`redact_rendered`/`split_config`/`decide_flag`/`ConfigStore.{applied,staged,stage,clear_staged,staged_count,fold,seed_applied}`/`apply_config`/`pending_status`/`ApplyError`/`write_config_atomic` consistent across tasks.

## Follow-on
v3.2 (the `/api/config/*` endpoints over this engine, replacing v2 config routes) and v3.3 (frontend rewiring + passthrough editor + rendered preview) — written after this engine is built + verified.
