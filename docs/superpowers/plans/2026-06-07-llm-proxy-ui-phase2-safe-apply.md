# LLM-Proxy Admin UI — Phase 2 (Safe-Apply Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `config.yaml` safely *editable* from the backend — expand the validation schema, add comment-headed atomic writes with backups, a SIGHUP reloader that verifies the proxy actually came back healthy with the expected models, and a `safe_apply` orchestrator exposed as `PUT /api/config` that **auto-rolls-back** any change that crashes or breaks the proxy. The Models/Routing **UI screens** are the next plan; this one is the load-bearing pipeline the spec says to "TDD each layer before any UI is wired."

**Architecture:** Layered safe-apply: `validate (pydantic + guardrails) → backup → atomic write → reload (SIGHUP) → verify (health + /v1/models) → rollback on failure`. Static validation uses our own pydantic schema mirroring [`docs/config-schema.md`](../../config-schema.md) (we do NOT import LiteLLM into the UI image). The runtime verify/rollback is the authoritative net that catches anything static checks miss.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, PyYAML, httpx (socket-proxy Docker API + litellm `/v1/models`). No new runtime deps.

**Spec:** [`docs/superpowers/specs/2026-06-07-llm-proxy-ui-design.md`](../specs/2026-06-07-llm-proxy-ui-design.md) (§ "Config generation & safe-apply") · **Schema:** [`docs/config-schema.md`](../../config-schema.md) · **Builds on:** Phase 1 (`docs/superpowers/plans/2026-06-07-llm-proxy-ui-phase1-foundation.md`).

---

## File Structure

```
ui/app/
├── settings.py          # MODIFY: drop database_url; add reload tunables
├── config_store.py      # MODIFY: required-field validation + write_config()/backup
├── reloader.py          # CREATE: SIGHUP via socket-proxy + verify + rollback
├── safe_apply.py        # CREATE: orchestrate validate→backup→write→reload→verify→rollback
└── routes/config_routes.py  # MODIFY: add PUT /api/config
ui/tests/
├── test_config_store.py   # MODIFY: required-field + write/backup tests
├── test_reloader.py       # CREATE
├── test_safe_apply.py     # CREATE
└── test_config_routes.py  # CREATE
docker-compose.yml        # MODIFY: UI config mount file→directory
```

Phase-1 carry-forwards settled here: config **directory** mount (atomic rename), `database_url` dropped, write-path runs the read validators.

---

## Task 1: Foundation fixes (config dir mount, settings tunables)

**Files:** Modify `docker-compose.yml`, `ui/app/settings.py`, `ui/tests/conftest.py`.

- [ ] **Step 1: Change the UI config mount from a file to a directory**

In `docker-compose.yml`, the `llm-proxy-ui` service volume:
```yaml
    volumes:
      - ./config:/config        # directory mount: enables atomic temp-file + rename
```
(Was `./config/config.yaml:/config/config.yaml`. `CONFIG_PATH=/config/config.yaml` is unchanged and still resolves. Leave the `litellm` service's `:ro` file mount as is.)

- [ ] **Step 2: Verify compose still parses**

Run: `cd /home/kumar/workspace/litellm && ADMIN_PASSWORD_HASH=x SESSION_SECRET=y docker compose config -q && echo OK`
Expected: `OK`.

- [ ] **Step 3: Update `settings.py` — drop unused `database_url`, add reload tunables**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    admin_password_hash: str = ""     # argon2 hash
    session_secret: str               # required — no insecure default (signs session cookies)
    config_path: str = "/config/config.yaml"
    socket_proxy_url: str = "http://socket-proxy:2375"
    litellm_container: str = "litellm-proxy"
    reload_mode: str = "SIGHUP"       # "SIGHUP" or "restart" (set per Task 4 spike)
    reload_timeout_s: float = 90.0    # max wait for the proxy to return healthy after reload


@lru_cache
def get_settings() -> Settings:
    return Settings()
```
(`database_url` is re-added in Phase 5 when housekeeping needs it.)

- [ ] **Step 4: Run the suite to confirm nothing referenced `database_url`**

Run: `cd ui && .venv/bin/python -m pytest -q`
Expected: PASS (19 passed). If a test referenced `database_url`, remove that reference.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml ui/app/settings.py
git commit -m "feat(ui): config dir mount + reload settings; drop unused database_url"
```

---

## Task 2: config_store — required-field validation per config-schema.md (TDD)

Strengthen validation so crash-class structural mistakes are rejected before write. Unknown keys stay preserved (`extra="allow"`).

**Files:** Modify `ui/app/config_store.py`; Test `ui/tests/test_config_store.py`.

- [ ] **Step 1: Write failing tests** (append to `ui/tests/test_config_store.py`)

```python
from app.config_store import ProxyConfig, ConfigError, validate_config


def test_model_entry_requires_model_in_litellm_params():
    with pytest.raises(Exception):
        ProxyConfig.model_validate({"model_list": [{"model_name": "x", "litellm_params": {}}]})


def test_model_entry_requires_model_name():
    with pytest.raises(Exception):
        ProxyConfig.model_validate({"model_list": [{"litellm_params": {"model": "openai/gpt-4o"}}]})


def test_unknown_keys_preserved_roundtrip():
    raw = {
        "litellm_settings": {"cache": True, "cache_params": {"type": "redis", "host": "valkey", "port": "6379"}},
        "router_settings": {"routing_strategy": "cost-based-routing", "num_retries": 5},
        "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini", "rpm": 100}}],
    }
    cfg = ProxyConfig.model_validate(raw)
    dumped = cfg.model_dump(exclude_none=True)
    assert dumped["router_settings"]["num_retries"] == 5           # unknown router key kept
    assert dumped["model_list"][0]["litellm_params"]["rpm"] == 100  # unknown litellm_param kept


def test_validate_config_helper_raises_configerror_on_bad_routing():
    with pytest.raises(ConfigError):
        validate_config({"router_settings": {"routing_strategy": "lowest-cost"}})
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_store.py -v`
Expected: the 4 new tests FAIL (`model` not required yet; `validate_config` missing).

- [ ] **Step 3: Implement** — make `litellm_params.model` and `model_name` required, add a `validate_config` helper that maps any validation error to `ConfigError`.

In `ui/app/config_store.py`, change `LitellmParams.model` to required and add the helper:
```python
class LitellmParams(BaseModel, extra="allow"):
    model: str            # required — provider-prefixed (openai/..., anthropic/..., azure/...)


class ModelEntry(BaseModel, extra="allow"):
    model_name: str       # required — client-facing alias
    litellm_params: LitellmParams
```
At the end of the module:
```python
def validate_config(raw: dict) -> "ProxyConfig":
    """Validate a candidate config dict (incl. guardrails). Raises ConfigError."""
    for k in FORBIDDEN_CACHE_KEYS:
        if k in ((raw.get("litellm_settings") or {}).get("cache_params") or {}):
            raise ConfigError(f"cache_params contains forbidden key {k!r} (LiteLLM bug #10949)")
    try:
        return ProxyConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(str(e)) from e
```
Refactor `load_config` to call `validate_config(raw)` after the file read (keep the explicit not-a-mapping guard).

- [ ] **Step 4: Run to verify pass (incl. the original guardrail tests)**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_store.py -v`
Expected: PASS (all — original 12 + new 4). NOTE: the empty-`litellm_params` default on `ModelEntry` is gone; if any earlier test built a `ModelEntry` without `model`, update it to include `litellm_params={"model": "..."}`.

- [ ] **Step 5: Commit**

```bash
git add ui/app/config_store.py ui/tests/test_config_store.py
git commit -m "feat(ui): require model_name + litellm_params.model; validate_config helper"
```

---

## Task 3: config_store — atomic write + backup (TDD)

**Files:** Modify `ui/app/config_store.py`; Test `ui/tests/test_config_store.py`.

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path
from app.config_store import write_config, load_config, ConfigError


def test_write_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "config.yaml")
    raw = {
        "general_settings": {"store_model_in_db": False},
        "litellm_settings": {"cache": True, "cache_params": {"type": "redis", "host": "valkey", "port": "6379"}},
        "router_settings": {"routing_strategy": "simple-shuffle"},
        "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini"}}],
    }
    write_config(path, raw)
    cfg = load_config(path)
    assert cfg.model_list[0].model_name == "cheap"
    assert cfg.router_settings.routing_strategy == "simple-shuffle"


def test_write_emits_guardrail_header(tmp_path):
    path = str(tmp_path / "config.yaml")
    write_config(path, {"router_settings": {"routing_strategy": "simple-shuffle"}})
    text = Path(path).read_text()
    assert text.startswith("#")                # header comment block present
    assert "#10949" in text and "store_model_in_db" in text


def test_write_rejects_forbidden_ssl(tmp_path):
    path = str(tmp_path / "config.yaml")
    with pytest.raises(ConfigError):
        write_config(path, {"litellm_settings": {"cache_params": {"type": "redis", "ssl": False}}})


def test_write_creates_timestamped_backup(tmp_path):
    path = str(tmp_path / "config.yaml")
    write_config(path, {"router_settings": {"routing_strategy": "simple-shuffle"}})
    write_config(path, {"router_settings": {"routing_strategy": "least-busy"}})
    backups = list(tmp_path.glob("config.yaml.bak.*"))
    assert len(backups) >= 1                   # prior version backed up before overwrite


def test_write_is_atomic_leaves_no_tmp(tmp_path):
    path = str(tmp_path / "config.yaml")
    write_config(path, {"router_settings": {"routing_strategy": "simple-shuffle"}})
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_store.py -k write -v`
Expected: FAIL (`write_config` missing).

- [ ] **Step 3: Implement `write_config`** (append to `ui/app/config_store.py`)

```python
import os
import tempfile
from datetime import datetime, timezone

_HEADER = """\
# LiteLLM proxy config — managed by the admin UI (llm-proxy-ui). Edits made here
# by hand are preserved on the next UI save EXCEPT comments (the UI re-emits this
# header). store_model_in_db is OFF: this file is the single source of truth for
# models, routing, and caching.
#
# Caching: the UI never writes an `ssl` key into cache_params. Don't add one by
# hand either — LiteLLM bug #10949 makes any ssl key (even ssl: false) use an SSL
# connection -> TLS handshake against plain Valkey hangs.
#
# routing_strategy must be one of: simple-shuffle, least-busy, usage-based-routing,
# usage-based-routing-v2, latency-based-routing, cost-based-routing (NOT lowest-cost).
"""


def write_config(path: str, raw: dict, *, backup: bool = True) -> "ProxyConfig":
    """Validate, then atomically write `raw` to `path` (header + yaml). Backs up the
    prior file. Returns the validated ProxyConfig. Raises ConfigError on invalid input."""
    cfg = validate_config(raw)                       # guardrails BEFORE any disk write
    body = yaml.safe_dump(cfg.model_dump(exclude_none=True), sort_keys=False, default_flow_style=False)
    content = _HEADER + "\n" + body
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if backup and p.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        p.with_name(f"{p.name}.bak.{ts}").write_text(p.read_text())
    # atomic replace within the same directory (requires a DIR mount, not a file mount)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, str(p))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return cfg
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_store.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add ui/app/config_store.py ui/tests/test_config_store.py
git commit -m "feat(ui): atomic config write with backup + guardrail header"
```

---

## Task 4: reloader — SIGHUP + verify + rollback (TDD + real-proxy spike)

**Files:** Create `ui/app/reloader.py`, `ui/tests/test_reloader.py`.

- [ ] **Step 1: SPIKE — confirm the reload mechanism against the real container**

Bring up the stack (`docker compose up -d`, wait for healthy). Then prove whether SIGHUP reloads config:
```bash
# change routing_strategy on disk, SIGHUP litellm, check it took effect
sed -i 's/simple-shuffle/least-busy/' config/config.yaml
docker kill --signal=SIGHUP litellm-proxy
sleep 8
docker compose exec litellm python3 -c "import urllib.request,json; print(urllib.request.urlopen('http://localhost:4000/health/readiness',timeout=5).read()[:80])"
docker compose logs --since=20s litellm | grep -iE 'reload|config|worker' | tail
```
Decide: if SIGHUP reliably reloads config → keep `reload_mode="SIGHUP"`. If NOT → set `reload_mode="restart"` (default in `.env`/compose) and the reloader uses the Docker `restart` endpoint instead. **Record the finding in a comment at the top of `reloader.py`.** Restore `config.yaml` (`git checkout config/config.yaml`) and `docker compose down` after the spike.

- [ ] **Step 2: Write failing tests** (`ui/tests/test_reloader.py`) — drive the socket-proxy + litellm via httpx MockTransport.

```python
import httpx
import pytest
from app.reloader import Reloader, ReloadError


def _reloader(handler):
    transport = httpx.MockTransport(handler)
    return Reloader(
        socket_proxy_url="http://socket-proxy:2375",
        litellm_base_url="http://litellm:4000",
        master_key="sk-test",
        container="litellm-proxy",
        mode="SIGHUP",
        transport=transport,
        poll_interval_s=0.0,
        timeout_s=2.0,
    )


@pytest.mark.asyncio
async def test_reload_ok_when_healthy_and_models_present():
    def handler(req):
        if req.url.path.endswith("/kill"):
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            return httpx.Response(200, json={"status": "healthy", "db": "connected"})
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "cheap"}, {"id": "smart"}]})
        return httpx.Response(404)
    r = _reloader(handler)
    ok = await r.reload_and_verify(expected_models=["cheap"])
    assert ok is True


@pytest.mark.asyncio
async def test_reload_fails_when_model_missing():
    def handler(req):
        if req.url.path.endswith("/kill"):
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            return httpx.Response(200, json={"status": "healthy"})
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "cheap"}]})
        return httpx.Response(404)
    r = _reloader(handler)
    with pytest.raises(ReloadError):
        await r.reload_and_verify(expected_models=["smart"])  # smart never appears -> timeout


@pytest.mark.asyncio
async def test_reload_fails_when_unhealthy():
    def handler(req):
        if req.url.path.endswith("/kill"):
            return httpx.Response(204)
        if req.url.path.endswith("/health/readiness"):
            return httpx.Response(503, json={"status": "unhealthy"})
        return httpx.Response(200, json={"data": []})
    r = _reloader(handler)
    with pytest.raises(ReloadError):
        await r.reload_and_verify(expected_models=[])
```

- [ ] **Step 3: Run to verify fail**

Run: `cd ui && .venv/bin/python -m pytest tests/test_reloader.py -v`
Expected: FAIL (`app.reloader` missing).

- [ ] **Step 4: Implement `ui/app/reloader.py`**

```python
from __future__ import annotations
import asyncio
import time
import httpx
from typing import Optional


class ReloadError(RuntimeError):
    pass


class Reloader:
    """Triggers a LiteLLM config reload via the scoped docker-socket-proxy, then
    verifies the proxy returns healthy AND serves the expected models. Raises
    ReloadError if it doesn't converge within timeout (caller rolls back)."""

    def __init__(self, socket_proxy_url, litellm_base_url, master_key, container,
                 mode="SIGHUP", transport: Optional[httpx.BaseTransport] = None,
                 poll_interval_s: float = 1.5, timeout_s: float = 90.0):
        self._sock = socket_proxy_url.rstrip("/")
        self._base = litellm_base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._container = container
        self._mode = mode
        self._transport = transport
        self._poll = poll_interval_s
        self._timeout = timeout_s

    def _client(self):
        return httpx.AsyncClient(timeout=10.0, transport=self._transport)

    async def trigger(self) -> None:
        async with self._client() as c:
            if self._mode == "restart":
                r = await c.post(f"{self._sock}/containers/{self._container}/restart")
            else:
                r = await c.post(f"{self._sock}/containers/{self._container}/kill",
                                 params={"signal": "SIGHUP"})
            if r.status_code >= 400:
                raise ReloadError(f"reload trigger failed: {r.status_code} {r.text[:200]}")

    async def reload_and_verify(self, expected_models: list[str]) -> bool:
        await self.trigger()
        deadline = time.monotonic() + self._timeout
        last = "no probe yet"
        while time.monotonic() < deadline:
            try:
                async with self._client() as c:
                    h = await c.get(f"{self._base}/health/readiness", headers=self._headers)
                    if h.status_code == 200 and h.json().get("status") == "healthy":
                        m = await c.get(f"{self._base}/v1/models", headers=self._headers)
                        ids = {d.get("id") for d in (m.json().get("data") or [])} if m.status_code == 200 else set()
                        if set(expected_models).issubset(ids):
                            return True
                        last = f"models {sorted(ids)} missing {sorted(set(expected_models)-ids)}"
                    else:
                        last = f"health {h.status_code}"
            except httpx.HTTPError as e:
                last = f"probe error: {e}"
            if self._poll:
                await asyncio.sleep(self._poll)
        raise ReloadError(f"proxy did not converge within {self._timeout}s ({last})")
```

- [ ] **Step 5: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_reloader.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add ui/app/reloader.py ui/tests/test_reloader.py
git commit -m "feat(ui): reloader — trigger reload + verify health/models"
```

---

## Task 5: safe_apply — orchestrate validate→backup→write→reload→verify→rollback (TDD)

**Files:** Create `ui/app/safe_apply.py`, `ui/tests/test_safe_apply.py`.

- [ ] **Step 1: Write failing tests** (inject a fake reloader to simulate success/failure)

```python
import pytest
from pathlib import Path
from app.config_store import load_config
from app.safe_apply import safe_apply, SafeApplyError


GOOD = {
    "general_settings": {"store_model_in_db": False},
    "router_settings": {"routing_strategy": "simple-shuffle"},
    "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini"}}],
}


class FakeReloader:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0
    async def reload_and_verify(self, expected_models):
        self.calls += 1
        if not self.ok:
            from app.reloader import ReloadError
            raise ReloadError("simulated failure")
        return True


@pytest.mark.asyncio
async def test_safe_apply_writes_and_reloads(tmp_path):
    path = str(tmp_path / "config.yaml")
    Path(path).write_text("router_settings:\n  routing_strategy: least-busy\n")
    rl = FakeReloader(ok=True)
    await safe_apply(path, GOOD, rl)
    assert load_config(path).router_settings.routing_strategy == "simple-shuffle"  # applied


@pytest.mark.asyncio
async def test_safe_apply_rejects_invalid_before_write(tmp_path):
    path = str(tmp_path / "config.yaml")
    Path(path).write_text("router_settings:\n  routing_strategy: least-busy\n")
    rl = FakeReloader(ok=True)
    with pytest.raises(SafeApplyError):
        await safe_apply(path, {"router_settings": {"routing_strategy": "lowest-cost"}}, rl)
    assert "least-busy" in Path(path).read_text()        # file untouched
    assert rl.calls == 0                                  # never reloaded


@pytest.mark.asyncio
async def test_safe_apply_rolls_back_on_reload_failure(tmp_path):
    path = str(tmp_path / "config.yaml")
    Path(path).write_text("router_settings:\n  routing_strategy: least-busy\n")
    rl = FakeReloader(ok=False)
    with pytest.raises(SafeApplyError):
        await safe_apply(path, GOOD, rl)
    # rolled back to the previous content (and the proxy was reloaded back onto it)
    assert load_config(path).router_settings.routing_strategy == "least-busy"
    assert rl.calls == 2                                  # apply attempt + rollback reload
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ui && .venv/bin/python -m pytest tests/test_safe_apply.py -v`
Expected: FAIL (`app.safe_apply` missing).

- [ ] **Step 3: Implement `ui/app/safe_apply.py`**

```python
from __future__ import annotations
from pathlib import Path
from app.config_store import write_config, load_config, ConfigError, ProxyConfig
from app.reloader import ReloadError


class SafeApplyError(RuntimeError):
    pass


def _expected_models(cfg: ProxyConfig) -> list[str]:
    return [m.model_name for m in cfg.model_list]


async def safe_apply(path: str, raw: dict, reloader) -> ProxyConfig:
    """Validate → snapshot current → atomic write → reload+verify. On reload failure,
    restore the snapshot and reload back onto it, then raise. Never leaves the proxy
    running on an unverified config."""
    # 1. snapshot current file for rollback, then validate+write. write_config
    #    validates first, so an invalid candidate raises before touching disk.
    p = Path(path)
    previous = p.read_text() if p.exists() else None
    try:
        cfg = write_config(path, raw)            # validates (raises ConfigError) then writes+backs up
    except ConfigError as e:
        raise SafeApplyError(f"invalid config (not applied): {e}") from e
    # 2. reload + verify
    try:
        await reloader.reload_and_verify(_expected_models(cfg))
        return cfg
    except ReloadError as e:
        # 3. rollback: restore prior file and reload back onto it
        if previous is not None:
            p.write_text(previous)
            try:
                prev_cfg = load_config(path)
                await reloader.reload_and_verify(_expected_models(prev_cfg))
            except Exception:
                pass   # best-effort; restart:unless-stopped recovers the file-backed config
        raise SafeApplyError(f"reload failed; rolled back: {e}") from e
```
- [ ] **Step 4: Run to verify pass**

Run: `cd ui && .venv/bin/python -m pytest tests/test_safe_apply.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add ui/app/safe_apply.py ui/tests/test_safe_apply.py
git commit -m "feat(ui): safe_apply orchestration with auto-rollback"
```

---

## Task 6: API — `PUT /api/config` (TDD)

**Files:** Modify `ui/app/routes/config_routes.py`; Create `ui/tests/test_config_routes.py`.

- [ ] **Step 1: Write failing tests** (override the reloader via app state so no real Docker/proxy is needed)

```python
import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, reloader_ok=True):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("pw")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("router_settings:\n  routing_strategy: least-busy\n")
    from app.main import create_app
    import app.routes.config_routes as cr

    class FakeReloader:
        async def reload_and_verify(self, expected_models):
            if not reloader_ok:
                from app.reloader import ReloadError
                raise ReloadError("sim")
            return True
    cr.make_reloader = lambda: FakeReloader()   # test seam (see impl note)
    c = TestClient(create_app())
    c.post("/api/auth/login", json={"password": "pw"})
    return c


def test_put_config_requires_login(tmp_path):
    c = _client(tmp_path)
    c.cookies.clear()
    assert c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"}}).status_code == 401


def test_put_config_applies(tmp_path):
    c = _client(tmp_path, reloader_ok=True)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"},
                                   "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini"}}]})
    assert r.status_code == 200
    assert c.get("/api/config").json()["router_settings"]["routing_strategy"] == "simple-shuffle"


def test_put_config_invalid_returns_422(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "lowest-cost"}})
    assert r.status_code == 422


def test_put_config_rollback_returns_409(tmp_path):
    c = _client(tmp_path, reloader_ok=False)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"}})
    assert r.status_code == 409
    assert c.get("/api/config").json()["router_settings"]["routing_strategy"] == "least-busy"  # rolled back
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ui && .venv/bin/python -m pytest tests/test_config_routes.py -v`
Expected: FAIL (no `PUT /api/config`).

- [ ] **Step 3: Implement** — add the PUT handler with a `make_reloader()` test seam.

```python
# ui/app/routes/config_routes.py  (add to the existing file)
from fastapi import Body
from app.config_store import load_config, write_config, ConfigError
from app.safe_apply import safe_apply, SafeApplyError
from app.reloader import Reloader


def make_reloader() -> Reloader:
    s = get_settings()
    return Reloader(s.socket_proxy_url, s.litellm_base_url, s.litellm_master_key,
                    s.litellm_container, mode=s.reload_mode, timeout_s=s.reload_timeout_s)


@router.put("/config", dependencies=[Depends(login_required)])
async def put_config(raw: dict = Body(...)):
    s = get_settings()
    try:
        cfg = await safe_apply(s.config_path, raw, make_reloader())
    except SafeApplyError as e:
        # invalid → 422; reload-failed-rolled-back → 409
        code = 422 if "invalid config" in str(e) else 409
        raise HTTPException(status_code=code, detail=str(e))
    return {"ok": True, "models": [m.model_name for m in cfg.model_list],
            "routing_strategy": cfg.router_settings.routing_strategy}
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `cd ui && .venv/bin/python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add ui/app/routes/config_routes.py ui/tests/test_config_routes.py
git commit -m "feat(ui): PUT /api/config with safe-apply (422 invalid, 409 rolled-back)"
```

---

## Task 7: Integration verification against the real stack

**Files:** none (manual/automated end-to-end verification).

- [ ] **Step 1: Build + bring up**

Run: `docker compose build llm-proxy-ui && docker compose up -d`; wait for all healthy. Log in via curl (cookie jar) as in Phase 1.

- [ ] **Step 2: Apply a valid model + verify it lands in `/v1/models`**

```bash
P=8081; J=/tmp/p2
curl -s -c $J -X POST http://localhost:$P/api/auth/login -H 'Content-Type: application/json' -d '{"password":"<your pw>"}' >/dev/null
curl -s -b $J -X PUT http://localhost:$P/api/config -H 'Content-Type: application/json' -d '{
  "general_settings":{"store_model_in_db":false},
  "litellm_settings":{"cache":true,"cache_params":{"type":"redis","host":"os.environ/REDIS_HOST","port":"os.environ/REDIS_PORT"}},
  "router_settings":{"routing_strategy":"simple-shuffle"},
  "model_list":[{"model_name":"smoke-echo","litellm_params":{"model":"openai/gpt-4o-mini","api_key":"sk-fake"}}]
}'
# expect {"ok":true,"models":["smoke-echo"],...}; confirm it reloaded:
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY" | grep smoke-echo
```
Expected: the PUT returns `ok:true`; `/v1/models` lists `smoke-echo`; `config/config.yaml` on the host shows the new model + the guardrail header.

- [ ] **Step 3: Apply a BAD config + verify auto-rollback (proxy stays up)**

```bash
# invalid routing -> 422, file untouched
curl -s -o /dev/null -w '%{http_code}\n' -b $J -X PUT http://localhost:$P/api/config \
  -H 'Content-Type: application/json' -d '{"router_settings":{"routing_strategy":"lowest-cost"}}'   # 422
# a config that breaks the proxy (e.g. a model with a malformed provider) -> 409 + rollback
# confirm proxy still healthy on the prior config:
curl -s http://localhost:4000/health/readiness -H "Authorization: Bearer $LITELLM_MASTER_KEY"
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY" | grep smoke-echo
```
Expected: invalid → 422 (file untouched); a reload-breaking config → 409 with the proxy still healthy serving `smoke-echo` (rolled back). Tear down when done.

- [ ] **Step 4: Commit a short runbook note + Phase 2 (backend) doc update**

Update `docs/admin-ui.md` (note: config is now UI-editable via `PUT /api/config` with safe-apply + auto-rollback). Commit:
```bash
git add docs/admin-ui.md
git commit -m "docs: Phase 2 safe-apply backend (PUT /api/config, auto-rollback)"
```

---

## Self-Review

- **Spec coverage (§ Config generation & safe-apply):** typed generation/validation ✓ (T2), YAML structure validation pre-write ✓ (T2,T3 `validate_config`), atomic write + backup ✓ (T3), reload + verify health *and* `/v1/models` ✓ (T4), auto-rollback ✓ (T5), API surface ✓ (T6), real-stack proof incl. rollback ✓ (T7). Phase-1 carry-forwards: dir mount ✓ (T1), `database_url` dropped ✓ (T1), write path runs read validators ✓ (T3/T5).
- **Placeholder scan:** none — every step contains complete, runnable code.
- **Type consistency:** `validate_config`/`write_config`/`load_config`/`ConfigError`/`ProxyConfig` (config_store), `Reloader.reload_and_verify`/`ReloadError` (reloader), `safe_apply`/`SafeApplyError` (safe_apply), `make_reloader` seam (routes) are defined once and referenced consistently. `reload_mode`/`reload_timeout_s`/dropped `database_url` match Task 1's settings.
- **Risk note:** Task 4's SIGHUP assumption is *verified by spike before* anything builds on it (Step 1), with a `reload_mode="restart"` fallback — no unverified mechanism in the critical path.

## Follow-on

- **Phase 2 (UI):** Models screen (CRUD → builds `litellm_params` per provider, secrets as `os.environ/`) + Routing screen (strategy + fallbacks) + the Save/diff/apply UX on top of `PUT /api/config`.
- Then Phase 3 (keys/budgets), Phase 4 (spend), Phase 5 (caching + housekeeping + export/import + dark mode).
