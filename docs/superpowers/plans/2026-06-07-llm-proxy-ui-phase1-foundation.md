# LLM-Proxy Admin UI — Phase 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `llm-proxy-ui` container — an authenticated FastAPI + Svelte app that reads & validates `config.yaml` (with the SSL/routing guardrails) and shows proxy health — wired into the compose stack alongside a scoped `socket-proxy`.

**Architecture:** FastAPI backend serves a built Svelte SPA from one container. Config is read from the bind-mounted `config.yaml` (write comes in Phase 2). Auth is an admin password (argon2 hash in env) + signed session cookie; the LiteLLM master key stays server-side. `store_model_in_db` is turned **off** so `config.yaml` is authoritative.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic v2, PyYAML, argon2-cffi, httpx, pytest; Svelte 5 + Vite; Docker multi-stage build; `tecnativa/docker-socket-proxy`.

**Spec:** `docs/superpowers/specs/2026-06-07-llm-proxy-ui-design.md` · **Visual:** `docs/superpowers/specs/2026-06-07-llm-proxy-ui-prototype.html`

---

## File Structure

```
ui/
├── Dockerfile                      # multi-stage: build Svelte → run FastAPI
├── pyproject.toml                  # backend deps + pytest config
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app factory, mounts routes + static
│   ├── settings.py                 # env-derived settings (pydantic-settings)
│   ├── auth.py                     # password verify, session, login_required
│   ├── config_store.py             # load/parse/validate config.yaml + guardrails
│   ├── litellm_client.py           # async client: proxy health
│   └── routes/
│       ├── __init__.py
│       ├── auth_routes.py          # /api/auth/login, /logout, /me
│       ├── health_routes.py        # /api/health (proxy + self)
│       └── config_routes.py        # /api/config (read-only in Phase 1)
├── tests/
│   ├── conftest.py
│   ├── test_config_store.py        # guardrails + parse/validate (TDD focus)
│   ├── test_auth.py
│   └── test_litellm_client.py
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.js
        ├── App.svelte              # shell: sidebar + router, or Login
        ├── lib/api.js              # fetch helpers (credentials: include)
        └── routes/
            ├── Login.svelte
            ├── Dashboard.svelte    # health pills + placeholder stats
            └── ConfigViewer.svelte # read-only config.yaml view
```

Compose/root changes: `docker-compose.yml`, `.env.example`,
`.releaserc.json`, `.github/workflows/release.yml`.

---

## Task 1: Scaffold the backend package + dependencies

**Files:**
- Create: `ui/pyproject.toml`
- Create: `ui/app/__init__.py` (empty)
- Create: `ui/app/settings.py`
- Create: `ui/tests/conftest.py`

- [ ] **Step 1: Create `ui/pyproject.toml`**

```toml
[project]
name = "llm-proxy-ui"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "pyyaml>=6.0",
    "argon2-cffi>=23.1",
    "httpx>=0.27",
    "itsdangerous>=2.2",
]

[dependency-groups]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 2: Create `ui/app/__init__.py`** (empty file)

- [ ] **Step 3: Create `ui/app/settings.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    admin_password_hash: str = ""     # argon2 hash
    session_secret: str = "change-me"
    config_path: str = "/config/config.yaml"
    socket_proxy_url: str = "http://socket-proxy:2375"
    litellm_container: str = "litellm-proxy"
    database_url: str = ""            # used from Phase 5 (housekeeping)


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Create `ui/tests/conftest.py`**

```python
import pytest
from app.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        admin_password_hash="",
        session_secret="test-secret",
        config_path=str(tmp_path / "config.yaml"),
    )
```

- [ ] **Step 5: Verify deps install**

Run: `cd ui && pip install -e . --group dev`
Expected: installs without error; `pytest --co` runs (collects 0 tests, exit 0 or 5).

- [ ] **Step 6: Commit**

```bash
git add ui/pyproject.toml ui/app/__init__.py ui/app/settings.py ui/tests/conftest.py
git commit -m "feat(ui): scaffold backend package + settings"
```

---

## Task 2: `config_store` — parse, validate, and the guardrails (TDD)

This is the highest-value unit: it must make the SSL-cache bug (#10949) and an
invalid routing strategy **impossible to produce**.

**Files:**
- Create: `ui/app/config_store.py`
- Test: `ui/tests/test_config_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# ui/tests/test_config_store.py
import pytest
import yaml
from app.config_store import load_config, ConfigError, VALID_ROUTING_STRATEGIES


def write(tmp_path, data):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return str(p)


def test_loads_valid_config(tmp_path):
    path = write(tmp_path, {
        "general_settings": {"store_model_in_db": False},
        "litellm_settings": {"cache": True, "cache_params": {"type": "redis", "host": "valkey", "port": "6379"}},
        "router_settings": {"routing_strategy": "cost-based-routing"},
        "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini"}}],
    })
    cfg = load_config(path)
    assert cfg.router_settings.routing_strategy == "cost-based-routing"
    assert cfg.model_list[0].model_name == "cheap"


def test_rejects_ssl_key_in_cache_params(tmp_path):
    path = write(tmp_path, {
        "litellm_settings": {"cache": True, "cache_params": {"type": "redis", "host": "valkey", "port": "6379", "ssl": False}},
    })
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "ssl" in str(e.value).lower()


def test_rejects_invalid_routing_strategy(tmp_path):
    path = write(tmp_path, {"router_settings": {"routing_strategy": "lowest-cost"}})
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "lowest-cost" in str(e.value)


def test_cost_based_routing_is_valid():
    assert "cost-based-routing" in VALID_ROUTING_STRATEGIES
    assert "lowest-cost" not in VALID_ROUTING_STRATEGIES


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "nope.yaml"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pytest tests/test_config_store.py -v`
Expected: FAIL with `ModuleNotFoundError: app.config_store`.

- [ ] **Step 3: Implement `ui/app/config_store.py`**

```python
from __future__ import annotations
import yaml
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, field_validator

VALID_ROUTING_STRATEGIES = {
    "simple-shuffle", "least-busy", "usage-based-routing",
    "usage-based-routing-v2", "latency-based-routing", "cost-based-routing",
}
# ssl keys that trigger LiteLLM bug #10949 — never allowed in cache_params
FORBIDDEN_CACHE_KEYS = {"ssl", "ssl_check_hostname"}


class ConfigError(ValueError):
    pass


class LitellmParams(BaseModel, extra="allow"):
    model: Optional[str] = None


class ModelEntry(BaseModel, extra="allow"):
    model_name: str
    litellm_params: LitellmParams = LitellmParams()


class RouterSettings(BaseModel, extra="allow"):
    routing_strategy: Optional[str] = None

    @field_validator("routing_strategy")
    @classmethod
    def _strategy(cls, v):
        if v is not None and v not in VALID_ROUTING_STRATEGIES:
            raise ValueError(
                f"invalid routing_strategy {v!r}; must be one of "
                f"{sorted(VALID_ROUTING_STRATEGIES)} (note: 'lowest-cost' is not valid)"
            )
        return v


class CacheParams(BaseModel, extra="allow"):
    type: Optional[str] = None

    @field_validator("*", mode="before")
    @classmethod
    def _no_ssl(cls, v, info):
        if info.field_name in FORBIDDEN_CACHE_KEYS:
            raise ValueError(
                f"cache_params must not contain {info.field_name!r} "
                "(LiteLLM bug #10949: any ssl key forces a TLS handshake that hangs against plain Valkey)"
            )
        return v

    @classmethod
    def model_validate_guarded(cls, data: dict):
        for k in FORBIDDEN_CACHE_KEYS:
            if k in (data or {}):
                raise ValueError(f"cache_params must not contain {k!r} (LiteLLM bug #10949)")
        return cls.model_validate(data or {})


class LitellmSettings(BaseModel, extra="allow"):
    cache: Optional[bool] = None
    cache_params: Optional[CacheParams] = None


class ProxyConfig(BaseModel, extra="allow"):
    general_settings: dict[str, Any] = {}
    litellm_settings: LitellmSettings = LitellmSettings()
    router_settings: RouterSettings = RouterSettings()
    model_list: list[ModelEntry] = []


def load_config(path: str) -> ProxyConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML: {e}") from e
    # explicit guardrail on cache_params before pydantic (clear message)
    cache_params = (raw.get("litellm_settings") or {}).get("cache_params") or {}
    for k in FORBIDDEN_CACHE_KEYS:
        if k in cache_params:
            raise ConfigError(
                f"cache_params contains forbidden key {k!r} (LiteLLM bug #10949)"
            )
    try:
        return ProxyConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(str(e)) from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pytest tests/test_config_store.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add ui/app/config_store.py ui/tests/test_config_store.py
git commit -m "feat(ui): config_store with ssl + routing-strategy guardrails"
```

---

## Task 3: `auth` — password verify + session (TDD)

**Files:**
- Create: `ui/app/auth.py`
- Test: `ui/tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

```python
# ui/tests/test_auth.py
from app.auth import hash_password, verify_password


def test_hash_then_verify_roundtrip():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h) is True


def test_wrong_password_fails():
    h = hash_password("s3cret")
    assert verify_password("nope", h) is False


def test_empty_hash_always_fails():
    assert verify_password("anything", "") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: app.auth`.

- [ ] **Step 3: Implement `ui/app/auth.py`**

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Request, HTTPException

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def login_required(request: Request) -> None:
    if not request.session.get("authed"):
        raise HTTPException(status_code=401, detail="login required")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pytest tests/test_auth.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add ui/app/auth.py ui/tests/test_auth.py
git commit -m "feat(ui): argon2 password auth helpers"
```

---

## Task 4: `litellm_client` — proxy health (TDD with mocked httpx)

**Files:**
- Create: `ui/app/litellm_client.py`
- Test: `ui/tests/test_litellm_client.py`

- [ ] **Step 1: Write the failing test**

```python
# ui/tests/test_litellm_client.py
import httpx
import pytest
from app.litellm_client import LitellmClient


@pytest.mark.asyncio
async def test_health_ok():
    def handler(request):
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"status": "healthy", "db": "connected"})

    transport = httpx.MockTransport(handler)
    client = LitellmClient("http://litellm:4000", "sk-test", transport=transport)
    health = await client.health()
    assert health["reachable"] is True
    assert health["raw"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_unreachable():
    def handler(request):
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(handler)
    client = LitellmClient("http://litellm:4000", "sk-test", transport=transport)
    health = await client.health()
    assert health["reachable"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && pytest tests/test_litellm_client.py -v`
Expected: FAIL with `ModuleNotFoundError: app.litellm_client`.

- [ ] **Step 3: Implement `ui/app/litellm_client.py`**

```python
from __future__ import annotations
import httpx
from typing import Any, Optional


class LitellmClient:
    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=8.0, transport=self._transport)

    async def health(self) -> dict[str, Any]:
        try:
            async with self._client() as c:
                r = await c.get(f"{self._base}/health/readiness")
                return {"reachable": True, "status_code": r.status_code, "raw": r.json()}
        except (httpx.HTTPError, ValueError) as e:
            return {"reachable": False, "error": str(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && pytest tests/test_litellm_client.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add ui/app/litellm_client.py ui/tests/test_litellm_client.py
git commit -m "feat(ui): litellm health client"
```

---

## Task 5: FastAPI app + routes + static serving

**Files:**
- Create: `ui/app/routes/__init__.py` (empty)
- Create: `ui/app/routes/auth_routes.py`
- Create: `ui/app/routes/health_routes.py`
- Create: `ui/app/routes/config_routes.py`
- Create: `ui/app/main.py`
- Test: append to `ui/tests/test_auth.py`

- [ ] **Step 1: Write the failing integration test (login flow)**

```python
# append to ui/tests/test_auth.py
import os
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path):
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("letmein")
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("general_settings: {}\n")
    from app.main import create_app
    return TestClient(create_app())


def test_health_requires_login(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/config").status_code == 401


def test_login_then_access(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": "letmein"}).status_code == 200
    assert c.get("/api/config").status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ui && pytest tests/test_auth.py -v`
Expected: FAIL (`app.main` / routes missing).

- [ ] **Step 3: Implement the route modules**

```python
# ui/app/routes/auth_routes.py
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.auth import verify_password
from app.settings import get_settings

router = APIRouter(prefix="/api/auth")


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request):
    s = get_settings()
    if not verify_password(body.password, s.admin_password_hash):
        raise HTTPException(status_code=401, detail="invalid password")
    request.session["authed"] = True
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"authed": bool(request.session.get("authed"))}
```

```python
# ui/app/routes/health_routes.py
from fastapi import APIRouter, Depends
from app.auth import login_required
from app.litellm_client import LitellmClient
from app.settings import get_settings

router = APIRouter(prefix="/api")


@router.get("/health", dependencies=[Depends(login_required)])
async def health():
    s = get_settings()
    client = LitellmClient(s.litellm_base_url, s.litellm_master_key)
    return {"ui": "ok", "proxy": await client.health()}
```

```python
# ui/app/routes/config_routes.py
from fastapi import APIRouter, Depends, HTTPException
from app.auth import login_required
from app.config_store import load_config, ConfigError
from app.settings import get_settings

router = APIRouter(prefix="/api")


@router.get("/config", dependencies=[Depends(login_required)])
def get_config():
    s = get_settings()
    try:
        cfg = load_config(s.config_path)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return cfg.model_dump(exclude_none=True)
```

- [ ] **Step 4: Implement `ui/app/main.py`**

```python
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.settings import get_settings
from app.routes import auth_routes, health_routes, config_routes

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="LLM Proxy UI")
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret,
                       same_site="lax", https_only=False)
    app.include_router(auth_routes.router)
    app.include_router(health_routes.router)
    app.include_router(config_routes.router)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd ui && pytest -v`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add ui/app/main.py ui/app/routes/
git commit -m "feat(ui): FastAPI app, auth/health/config routes, static mount"
```

---

## Task 6: Svelte frontend shell (login + Dashboard + config viewer)

**Files:**
- Create: `ui/frontend/package.json`, `vite.config.js`, `index.html`, `src/main.js`, `src/App.svelte`, `src/lib/api.js`, `src/routes/Login.svelte`, `src/routes/Dashboard.svelte`, `src/routes/ConfigViewer.svelte`

- [ ] **Step 1: Create `ui/frontend/package.json`**

```json
{
  "name": "llm-proxy-ui-frontend",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build" },
  "devDependencies": {
    "@sveltejs/vite-plugin-svelte": "^4.0.0",
    "svelte": "^5.0.0",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: Create `ui/frontend/vite.config.js`**

```javascript
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  // build to ./dist; the Docker image copies dist → the backend's app/static
  build: { outDir: 'dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://localhost:8080' } },
})
```

- [ ] **Step 3: Create `ui/frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>LLM Proxy</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

- [ ] **Step 4: Create `ui/frontend/src/main.js`**

```javascript
import App from './App.svelte'
import { mount } from 'svelte'
mount(App, { target: document.getElementById('app') })
```

- [ ] **Step 5: Create `ui/frontend/src/lib/api.js`**

```javascript
async function req(path, opts = {}) {
  const r = await fetch(path, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...opts })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText)
  return r.json()
}
export const api = {
  me: () => req('/api/auth/me'),
  login: (password) => req('/api/auth/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => req('/api/auth/logout', { method: 'POST' }),
  health: () => req('/api/health'),
  config: () => req('/api/config'),
}
```

- [ ] **Step 6: Create `ui/frontend/src/App.svelte`** (shell — the approved Apple-HIG layout from the prototype, trimmed to Phase-1 screens)

```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from './lib/api.js'
  import Login from './routes/Login.svelte'
  import Dashboard from './routes/Dashboard.svelte'
  import ConfigViewer from './routes/ConfigViewer.svelte'

  let authed = $state(false)
  let screen = $state('dash')
  onMount(async () => { authed = (await api.me()).authed })
  async function onLogin() { authed = true }
  async function logout() { await api.logout(); authed = false }
</script>

{#if !authed}
  <Login {onLogin} />
{:else}
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><span class="logo">LP</span> LLM Proxy</div>
      <div class="navgroup">Overview</div>
      <button class="nav" class:active={screen==='dash'} onclick={() => screen='dash'}>▦ Dashboard</button>
      <div class="navgroup">Configuration</div>
      <button class="nav" class:active={screen==='config'} onclick={() => screen='config'}>◈ config.yaml</button>
      <div class="spacer"></div>
      <button class="nav" onclick={logout}>⎋ Sign out</button>
    </aside>
    <main class="main">
      {#if screen==='dash'}<Dashboard />{:else}<ConfigViewer />{/if}
    </main>
  </div>
{/if}

<style>
  :global(body){margin:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;color:#1d1d1f;-webkit-font-smoothing:antialiased}
  .app{display:grid;grid-template-columns:236px 1fr;height:100vh}
  .sidebar{background:#f5f5f7;border-right:1px solid rgba(0,0,0,.08);padding:18px 12px;display:flex;flex-direction:column}
  .brand{display:flex;align-items:center;gap:9px;padding:2px 8px 16px;font-weight:600}
  .logo{width:24px;height:24px;border-radius:7px;background:linear-gradient(135deg,#0a84ff,#5e5ce6);color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700}
  .navgroup{margin:12px 6px 4px;font-size:11px;text-transform:uppercase;color:#6e6e73;font-weight:600}
  .nav{display:block;width:100%;text-align:left;border:0;background:none;padding:7px 10px;border-radius:8px;font:inherit;cursor:pointer}
  .nav.active{background:#0a84ff;color:#fff}
  .spacer{flex:1}
  .main{overflow:auto}
</style>
```

- [ ] **Step 7: Create `ui/frontend/src/routes/Login.svelte`**

```svelte
<script>
  import { api } from '../lib/api.js'
  let { onLogin } = $props()
  let password = $state(''); let error = $state('')
  async function submit(e) {
    e.preventDefault()
    try { await api.login(password); onLogin() } catch (err) { error = err.message }
  }
</script>
<div style="display:flex;align-items:center;justify-content:center;height:100vh;background:#f5f5f7">
  <form onsubmit={submit} style="background:#fff;padding:32px;border-radius:14px;box-shadow:0 10px 30px rgba(0,0,0,.1);width:320px">
    <h2 style="margin:0 0 16px">LLM Proxy</h2>
    <input type="password" bind:value={password} placeholder="Admin password"
      style="width:100%;padding:10px;border:1px solid #ccc;border-radius:8px;margin-bottom:12px" />
    {#if error}<p style="color:#ff3b30;font-size:13px">{error}</p>{/if}
    <button style="width:100%;padding:10px;background:#0a84ff;color:#fff;border:0;border-radius:8px;font-weight:600">Sign in</button>
  </form>
</div>
```

- [ ] **Step 8: Create `ui/frontend/src/routes/Dashboard.svelte`**

```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let health = $state(null); let err = $state('')
  onMount(async () => { try { health = await api.health() } catch (e) { err = e.message } })
  const ok = (b) => b ? '#34c759' : '#ff3b30'
</script>
<div style="padding:24px 30px;max-width:960px">
  <h1>Dashboard</h1>
  {#if err}<p style="color:#ff3b30">{err}</p>{/if}
  {#if health}
    <div style="display:flex;gap:14px;margin-top:12px">
      <div style="border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:14px 16px">
        <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{ok(health.proxy.reachable)}"></span>
        Proxy · {health.proxy.reachable ? 'reachable' : 'unreachable'}
      </div>
    </div>
    <pre style="margin-top:16px;background:#f5f5f7;padding:14px;border-radius:10px;font-size:12px;overflow:auto">{JSON.stringify(health, null, 2)}</pre>
  {/if}
</div>
```

- [ ] **Step 9: Create `ui/frontend/src/routes/ConfigViewer.svelte`**

```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let cfg = $state(null); let err = $state('')
  onMount(async () => { try { cfg = await api.config() } catch (e) { err = e.message } })
</script>
<div style="padding:24px 30px;max-width:960px">
  <h1>config.yaml <span style="font-size:13px;color:#6e6e73">(read-only — editing lands in Phase 2)</span></h1>
  {#if err}<p style="color:#ff3b30">{err}</p>{/if}
  {#if cfg}<pre style="background:#f5f5f7;padding:14px;border-radius:10px;font-size:12px;overflow:auto">{JSON.stringify(cfg, null, 2)}</pre>{/if}
</div>
```

- [ ] **Step 10: Build the frontend**

Run: `cd ui/frontend && npm install && npm run build`
Expected: builds into `ui/frontend/dist/` (creates `index.html` + assets). For
local full-stack dev, run `npm run dev` instead (Vite serves the SPA and proxies
`/api` to the backend on :8080).

- [ ] **Step 11: Commit**

```bash
git add ui/frontend/
git commit -m "feat(ui): Svelte shell — login, dashboard, config viewer"
```

---

## Task 7: Dockerfile + compose wiring + env

**Files:**
- Create: `ui/Dockerfile`, `ui/.dockerignore`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Create `ui/Dockerfile` (multi-stage)**

```dockerfile
# --- build frontend ---
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build           # vite build.outDir = 'dist' → /web/dist

# --- runtime ---
FROM python:3.12-slim AS runtime
WORKDIR /srv
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app/ ./app/
COPY --from=web /web/dist ./app/static
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/auth/me',timeout=4).status==200 else 1)"
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: Create `ui/.dockerignore`**

```
frontend/node_modules
app/static
__pycache__
tests
*.pyc
```

- [ ] **Step 3: Add services to `docker-compose.yml`** (append under `services:`)

```yaml
  socket-proxy:
    image: tecnativa/docker-socket-proxy:0.3
    container_name: litellm-socket-proxy
    restart: unless-stopped
    environment:
      CONTAINERS: "1"
      POST: "1"          # allow container POST actions (kill/SIGHUP) — used from Phase 2
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks: [default]

  llm-proxy-ui:
    build: ./ui
    container_name: litellm-ui
    restart: unless-stopped
    environment:
      LITELLM_BASE_URL: http://litellm:4000
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      ADMIN_PASSWORD_HASH: ${ADMIN_PASSWORD_HASH}
      SESSION_SECRET: ${SESSION_SECRET}
      CONFIG_PATH: /config/config.yaml
      SOCKET_PROXY_URL: http://socket-proxy:2375
      LITELLM_CONTAINER: litellm-proxy
    volumes:
      - ./config/config.yaml:/config/config.yaml      # RW (UI writes from Phase 2)
    ports:
      - "${UI_PORT:-8081}:8080"
    depends_on:
      litellm:
        condition: service_healthy
```

- [ ] **Step 4: Set `STORE_MODEL_IN_DB` to false on the litellm service**

Modify `docker-compose.yml` litellm `environment:` — change `STORE_MODEL_IN_DB: "true"` to `STORE_MODEL_IN_DB: "false"`.

- [ ] **Step 5: Append to `.env.example`**

```bash
# Admin UI
UI_PORT=8081
# argon2 hash of your admin password — generate with:
#   docker run --rm llm-proxy-ui python -c "from app.auth import hash_password; print(hash_password('YOUR_PASSWORD'))"
ADMIN_PASSWORD_HASH=
# random secret for signing session cookies (openssl rand -hex 32)
SESSION_SECRET=
```

- [ ] **Step 6: Verify compose parses**

Run: `docker compose config -q && echo OK`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add ui/Dockerfile ui/.dockerignore docker-compose.yml .env.example
git commit -m "feat(ui): Dockerfile + compose wiring (ui + scoped socket-proxy)"
```

---

## Task 8: Integration smoke test

**Files:** none (manual/automated verification)

- [ ] **Step 1: Generate an admin hash + secret into `.env`**

```bash
SESSION_SECRET=$(openssl rand -hex 32)
# build first so the helper is available:
docker compose build llm-proxy-ui
HASH=$(docker compose run --rm --no-deps llm-proxy-ui \
  python -c "from app.auth import hash_password; print(hash_password('letmein'))")
printf "UI_PORT=8081\nADMIN_PASSWORD_HASH=%s\nSESSION_SECRET=%s\n" "$HASH" "$SESSION_SECRET" >> .env
```

- [ ] **Step 2: Bring the stack up**

Run: `docker compose up -d && docker compose ps`
Expected: `litellm-ui` reaches `healthy`.

- [ ] **Step 3: Verify auth gate + login + health (from the dev box)**

```bash
P=${UI_PORT:-8081}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:$P/api/config            # 401
curl -s -c /tmp/j -X POST http://localhost:$P/api/auth/login \
  -H 'Content-Type: application/json' -d '{"password":"letmein"}'                    # {"ok":true}
curl -s -b /tmp/j http://localhost:$P/api/health | head -c 200                      # proxy health JSON
```
Expected: 401 unauthenticated; login ok; health JSON shows `proxy.reachable: true`.

- [ ] **Step 4: Verify the UI in a browser**

Open `http://10.0.20.85:8081` → login with `letmein` → Dashboard shows proxy reachable → config.yaml viewer renders the parsed config.

- [ ] **Step 5: Commit a short runbook note**

```bash
# (append a "Custom UI (Phase 1)" section to README.md describing UI_PORT, the
#  ADMIN_PASSWORD_HASH generation command, and the http://host:8081 login)
git add README.md
git commit -m "docs: Phase 1 admin UI runbook (build, hash, login)"
```

---

## Task 9: CI — semantic-release + GHCR image publish

**Files:**
- Create: `.releaserc.json`
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create `.releaserc.json`**

```json
{
  "branches": ["main"],
  "plugins": [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    "@semantic-release/changelog",
    ["@semantic-release/git", {
      "assets": ["CHANGELOG.md"],
      "message": "chore(release): ${nextRelease.version} [skip ci]"
    }],
    "@semantic-release/github"
  ]
}
```

- [ ] **Step 2: Create `.github/workflows/release.yml`**

```yaml
name: release
on:
  push:
    branches: [main]
permissions:
  contents: write      # tags, releases, CHANGELOG commit
  packages: write      # push to GHCR
  issues: write
  pull-requests: write
concurrency:
  group: release
  cancel-in-progress: false
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # semantic-release needs full history
      - name: Semantic release
        id: semantic
        uses: cycjimmy/semantic-release-action@v4
        with:
          extra_plugins: |
            @semantic-release/changelog
            @semantic-release/git
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - name: Log in to GHCR
        if: steps.semantic.outputs.new_release_published == 'true'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build & push UI image
        if: steps.semantic.outputs.new_release_published == 'true'
        uses: docker/build-push-action@v6
        with:
          context: ./ui
          push: true
          tags: |
            ghcr.io/tekgnosis-net/llm-proxy-ui:${{ steps.semantic.outputs.new_release_version }}
            ghcr.io/tekgnosis-net/llm-proxy-ui:latest
```

- [ ] **Step 3: Verify the workflow YAML is valid**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit (triggers the first release on push)**

```bash
git add .releaserc.json .github/workflows/release.yml
git commit -m "ci: semantic-release + GHCR image publish for llm-proxy-ui"
git push
```

- [ ] **Step 5: Verify on GitHub**

After push: Actions → `release` run is green; a GitHub Release + tag (e.g.
`v0.1.0`) is created; **Packages** shows `llm-proxy-ui` with `:<version>` and
`:latest`. (First run: ensure repo → Settings → Actions → Workflow permissions
allow "Read and write".)

---

## Self-Review

- **Spec coverage (Phase 1 slice):** container scaffold ✓ (T1,7), auth admin-password + server-side master key ✓ (T3,5,7), config-only `store_model_in_db:false` ✓ (T7.4), `config_store` read+validate + **guardrails** ✓ (T2), health/dashboard ✓ (T4,5,6), Apple-HIG shell + B framing ✓ (T6), socket-proxy defined (used Phase 2) ✓ (T7). CI/CD — semantic-release + GHCR
image publish ✓ (T9). Deferred by design to later phases: config **write**+reload (P2), keys (P3), spend (P4), caching+housekeeping+export (P5).
- **Placeholders:** none — every code step has full content. (The one prose **Note** in T7 is a real build-config instruction, not a placeholder.)
- **Type consistency:** `load_config`/`ConfigError`/`VALID_ROUTING_STRATEGIES`, `hash_password`/`verify_password`/`login_required`, `LitellmClient.health()`, `create_app()`, and `api.*` JS helpers are defined once and referenced consistently across tasks.

## Phase 2 must-settle-first (from Phase 1 final review + smoke test)

Settle these at the START of the Phase 2 plan — foundation choices, not Phase 1 defects:

1. **Mount the config *directory*, not the file.** Change the `llm-proxy-ui`
   volume from `./config/config.yaml:/config/config.yaml` to `./config:/config`
   (keep `CONFIG_PATH=/config/config.yaml`). A bind-mounted single file can't be
   atomically replaced via temp-file + `os.rename()` (fails `EXDEV`); a directory
   mount lets the UI write `config.yaml.tmp` and rename within the same fs.
2. **Decide `database_url`'s fate.** `settings.py` declares it but compose never
   passes it to the UI — wire it through (housekeeping/stats) or drop the field
   until needed.
3. **Run the write path through the read validators.** The save endpoint must
   feed incoming config through `load_config`/pydantic (guardrails) BEFORE
   persisting; add round-trip tests (write → reload → equal).
4. **`.env` `$$`-escaping for ADMIN_PASSWORD_HASH** is a known gotcha (smoke test;
   see `.env.example` + the `litellm-ui-admin-hash-env-escaping` memory).
5. **Minor/optional:** make `SessionMiddleware(https_only=...)` env-driven if the
   UI ever leaves the LAN; reconcile the README `LITELLM_PORT` reference with the
   host-local port tweak; consider dropping litellm's `4000:4000` host publish
   once the UI fully fronts it.

## Follow-on plans (one per phase)

- Phase 2 — Models + Routing editing + write/validate/**reload** (socket-proxy SIGHUP).
- Phase 3 — Virtual keys + budgets (API) incl. Create-Key sheet + per-key routing.
- Phase 4 — Usage & Spend dashboard.
- Phase 5 — Caching config + **DB housekeeping** (retention + maintenance cron + stats) + Export/Import snapshot + dark mode.
