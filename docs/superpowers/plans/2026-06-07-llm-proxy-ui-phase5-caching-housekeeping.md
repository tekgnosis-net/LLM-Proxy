# LLM-Proxy Admin UI — Phase 5 (Caching · Housekeeping · Export/Import · Dark mode) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD. DB maintenance verified on the real stack (destructive — handle carefully). Steps use `- [ ]`.

**Goal:** Close out the UI: (1) **Caching** screen editing `litellm_settings.cache_params` via safe-apply (never an `ssl` key); (2) **DB Housekeeping** — DB stats + admin-triggered/scheduled maintenance (trim old spend logs, delete expired keys) via APScheduler, plus the built-in LiteLLM retention setting; (3) **Export/Import** of `config.yaml`; (4) **Dark mode**.

**Architecture:** Caching/retention reuse the existing safe-apply `PUT /api/config`. Housekeeping adds `db_admin` (asyncpg) + `/api/housekeeping` routes + an opt-in `AsyncIOScheduler` cron started in the app lifespan. Maintenance is **conservative + explicit**: cron is off unless `HOUSEKEEPING_ENABLED=true`; retention defaults to 90 days; every run returns the row counts it removed. Export = `GET /api/config/export` (download); Import = the existing validated `PUT /api/config`. Dark mode = CSS variables + a persisted toggle.

**Tech Stack:** + `asyncpg`, `apscheduler`. Svelte 5.

**LiteLLM facts:** spend logs grow in `LiteLLM_SpendLogs` (cols incl. `"startTime"`); virtual keys in `LiteLLM_VerificationToken` (`expires`); built-in retention via `general_settings.maximum_spend_logs_retention_period` (e.g. `"90d"`) + `maximum_spend_logs_retention_interval` (e.g. `"1d"`). `cache_params` must NEVER contain `ssl` (#10949 — already guarded in config_store).

---

## File Structure
```
ui/pyproject.toml                 # + asyncpg, apscheduler
ui/app/settings.py                # re-add database_url; housekeeping_* settings
ui/app/db_admin.py                # CREATE: asyncpg stats + maintenance
ui/app/routes/housekeeping_routes.py  # CREATE
ui/app/routes/config_routes.py    # + GET /api/config/export
ui/app/main.py                    # include housekeeping_routes; lifespan cron
docker-compose.yml                # pass DATABASE_URL + HOUSEKEEPING_* to ui
ui/tests/test_housekeeping_routes.py  # CREATE
ui/tests/test_db_admin.py         # CREATE (plan/SQL builders)
ui/tests/test_config_routes.py    # + export test
ui/frontend/src/lib/api.js        # + caching/housekeeping/export helpers + theme
ui/frontend/src/routes/Caching.svelte      # CREATE
ui/frontend/src/routes/Housekeeping.svelte # CREATE
ui/frontend/src/routes/Settings.svelte     # CREATE (export/import + dark mode)
ui/frontend/src/App.svelte        # nav + render + theme init
```

---

## Task 1: deps + settings + compose

**Files:** `ui/pyproject.toml`, `ui/app/settings.py`, `docker-compose.yml`.

- [ ] **Step 1: pyproject** — add to `dependencies`: `"asyncpg>=0.30"`, `"apscheduler>=3.10"`. Then `cd ui && .venv/bin/pip install asyncpg apscheduler`.
- [ ] **Step 2: settings.py** — re-add `database_url` and housekeeping config:
```python
    database_url: str = ""            # asyncpg DSN for housekeeping/stats
    housekeeping_enabled: bool = False        # opt-in: scheduled maintenance cron
    housekeeping_interval_hours: int = 24
    housekeeping_spendlog_retention_days: int = 90
    housekeeping_delete_expired_keys: bool = True
```
- [ ] **Step 3: compose** — in the `llm-proxy-ui` service `environment:` add:
```yaml
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/litellm
      HOUSEKEEPING_ENABLED: "${HOUSEKEEPING_ENABLED:-false}"
      HOUSEKEEPING_INTERVAL_HOURS: "${HOUSEKEEPING_INTERVAL_HOURS:-24}"
      HOUSEKEEPING_SPENDLOG_RETENTION_DAYS: "${HOUSEKEEPING_SPENDLOG_RETENTION_DAYS:-90}"
```
Add the `llm-proxy-ui` `depends_on` postgres `condition: service_healthy` (it now needs the DB).
- [ ] **Step 4:** `ADMIN_PASSWORD_HASH=x SESSION_SECRET=y docker compose config -q && echo OK`; `cd ui && .venv/bin/python -m pytest -q` (60 pass). Commit `feat(ui): housekeeping deps + settings + DB env`.

---

## Task 2: db_admin (asyncpg stats + maintenance)

**Files:** Create `ui/app/db_admin.py`, `ui/tests/test_db_admin.py`.

- [ ] **Step 1: failing tests** — test the pure SQL/plan builders (no live DB):
```python
from app.db_admin import maintenance_sql, RETENTION_TABLE


def test_spendlog_trim_sql_is_parameterized_and_scoped():
    sql = maintenance_sql(retention_days=30)["trim_spend_logs"]
    assert '"LiteLLM_SpendLogs"' in sql
    assert "startTime" in sql
    assert "$1" in sql            # parameterized interval, no string interpolation of user data


def test_expired_keys_sql_targets_verification_token():
    sql = maintenance_sql(retention_days=30)["delete_expired_keys"]
    assert '"LiteLLM_VerificationToken"' in sql and "expires" in sql
```

- [ ] **Step 2: run red** → FAIL.

- [ ] **Step 3: implement `ui/app/db_admin.py`:**
```python
from __future__ import annotations
from typing import Any, Optional
import asyncpg

RETENTION_TABLE = '"LiteLLM_SpendLogs"'
STATS_TABLES = ['"LiteLLM_SpendLogs"', '"LiteLLM_VerificationToken"', '"LiteLLM_ErrorLogs"']


def maintenance_sql(retention_days: int) -> dict[str, str]:
    # $1 = retention_days (interval built safely via make_interval); never string-interpolate input
    return {
        "trim_spend_logs": f'DELETE FROM {RETENTION_TABLE} WHERE "startTime" < (now() - make_interval(days => $1))',
        "delete_expired_keys": 'DELETE FROM "LiteLLM_VerificationToken" WHERE "expires" IS NOT NULL AND "expires" < now()',
    }


class DbAdmin:
    def __init__(self, dsn: str):
        self._dsn = dsn

    async def _conn(self):
        return await asyncpg.connect(self._dsn)

    async def stats(self) -> dict[str, Any]:
        conn = await self._conn()
        try:
            rows = {}
            for t in STATS_TABLES:
                try:
                    rows[t.strip('"')] = await conn.fetchval(f"SELECT count(*) FROM {t}")
                except asyncpg.UndefinedTableError:
                    rows[t.strip('"')] = None
            db_size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
            return {"row_counts": rows, "db_size": db_size}
        finally:
            await conn.close()

    async def run_maintenance(self, retention_days: int, delete_expired_keys: bool = True) -> dict[str, Any]:
        sql = maintenance_sql(retention_days)
        conn = await self._conn()
        try:
            trimmed = await conn.execute(sql["trim_spend_logs"], retention_days)  # returns "DELETE <n>"
            result = {"trimmed_spend_logs": _count(trimmed), "retention_days": retention_days}
            if delete_expired_keys:
                result["deleted_expired_keys"] = _count(await conn.execute(sql["delete_expired_keys"]))
            return result
        finally:
            await conn.close()


def _count(tag: str) -> int:
    # asyncpg execute() returns a command tag like "DELETE 12"
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0
```

- [ ] **Step 4: green + full suite. Step 5: commit** `feat(ui): db_admin (asyncpg stats + maintenance SQL)`.

---

## Task 3: housekeeping routes + scheduled cron

**Files:** Create `ui/app/routes/housekeeping_routes.py`, `ui/tests/test_housekeeping_routes.py`; Modify `ui/app/main.py`.

- [ ] **Step 1: failing tests** (fake db_admin via `make_db_admin` seam):
```python
import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path / "c.yaml"), DATABASE_URL="postgresql://x")
    (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.housekeeping_routes as hk
    hk.make_db_admin = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


class FakeDb:
    def __init__(self): self.ran = None
    async def stats(self): return {"row_counts": {"LiteLLM_SpendLogs": 5}, "db_size": "12 MB"}
    async def run_maintenance(self, retention_days, delete_expired_keys=True):
        self.ran = (retention_days, delete_expired_keys); return {"trimmed_spend_logs": 3, "deleted_expired_keys": 1, "retention_days": retention_days}


def test_housekeeping_requires_login(tmp_path):
    c = _client(tmp_path, FakeDb()); c.cookies.clear()
    assert c.get("/api/housekeeping").status_code == 401


def test_housekeeping_stats(tmp_path):
    d = _client(tmp_path, FakeDb()).get("/api/housekeeping").json()
    assert d["stats"]["db_size"] == "12 MB"
    assert d["settings"]["retention_days"] == 90 and d["settings"]["enabled"] is False


def test_housekeeping_run(tmp_path):
    fake = FakeDb(); c = _client(tmp_path, fake)
    d = c.post("/api/housekeeping/run").json()
    assert d["trimmed_spend_logs"] == 3 and fake.ran == (90, True)
```

- [ ] **Step 2: run red** → FAIL.

- [ ] **Step 3: implement `ui/app/routes/housekeeping_routes.py`:**
```python
from fastapi import APIRouter, Depends, HTTPException
from app.auth import login_required
from app.db_admin import DbAdmin
from app.settings import get_settings

router = APIRouter(prefix="/api")


def make_db_admin() -> DbAdmin:
    s = get_settings()
    if not s.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    return DbAdmin(s.database_url)


def _settings_view():
    s = get_settings()
    return {"enabled": s.housekeeping_enabled, "interval_hours": s.housekeeping_interval_hours,
            "retention_days": s.housekeeping_spendlog_retention_days,
            "delete_expired_keys": s.housekeeping_delete_expired_keys}


@router.get("/housekeeping", dependencies=[Depends(login_required)])
async def housekeeping():
    try:
        stats = await make_db_admin().stats()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"DB error: {e}")
    return {"stats": stats, "settings": _settings_view()}


@router.post("/housekeeping/run", dependencies=[Depends(login_required)])
async def run_now():
    s = get_settings()
    try:
        return await make_db_admin().run_maintenance(s.housekeeping_spendlog_retention_days,
                                                      s.housekeeping_delete_expired_keys)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"DB error: {e}")
```

- [ ] **Step 4: wire cron in `ui/app/main.py`** — use a lifespan that starts an AsyncIOScheduler only when enabled:
```python
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.db_admin import DbAdmin

@asynccontextmanager
async def lifespan(app):
    s = get_settings()
    sched = None
    if s.housekeeping_enabled and s.database_url:
        sched = AsyncIOScheduler()
        async def job():
            try:
                await DbAdmin(s.database_url).run_maintenance(
                    s.housekeeping_spendlog_retention_days, s.housekeeping_delete_expired_keys)
            except Exception:
                pass   # cron is best-effort; manual run surfaces errors
        sched.add_job(job, "interval", hours=s.housekeeping_interval_hours, id="housekeeping")
        sched.start()
    try:
        yield
    finally:
        if sched:
            sched.shutdown(wait=False)
```
Pass `lifespan=lifespan` to `FastAPI(...)` in `create_app()`, and `app.include_router(housekeeping_routes.router)`.

- [ ] **Step 5: green + full suite. Step 6: commit** `feat(ui): /api/housekeeping (stats + maintenance) + opt-in cron`.

---

## Task 4: config export route

**Files:** Modify `ui/app/routes/config_routes.py`; add a test.

- [ ] **Step 1: failing test** (append to `test_config_routes.py`):
```python
def test_export_returns_yaml_attachment(tmp_path):
    c = _client(tmp_path)  # reuse the helper (logged in)
    r = c.get("/api/config/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "routing_strategy" in r.text or "model_list" in r.text
```

- [ ] **Step 2: implement** in `config_routes.py`:
```python
from fastapi.responses import PlainTextResponse
from pathlib import Path

@router.get("/config/export", dependencies=[Depends(login_required)])
def export_config():
    s = get_settings()
    text = Path(s.config_path).read_text()
    return PlainTextResponse(text, media_type="text/yaml",
                             headers={"Content-Disposition": 'attachment; filename="config.yaml"'})
```

- [ ] **Step 3: green + full suite. Step 4: commit** `feat(ui): GET /api/config/export (download config.yaml)`.

---

## Task 5: Frontend — Caching, Housekeeping, Settings(export/import + dark mode), nav, theme

**Files:** `ui/frontend/src/lib/api.js`, `ui/frontend/src/routes/{Caching,Housekeeping,Settings}.svelte`, `ui/frontend/src/App.svelte`.

- [ ] **Step 1: api.js helpers:**
```javascript
  housekeeping: () => req('/api/housekeeping'),
  runHousekeeping: () => req('/api/housekeeping/run', { method: 'POST' }),
  exportConfigUrl: '/api/config/export',
```

- [ ] **Step 2: `Caching.svelte`** — edits `litellm_settings` via the shared store (full round-trip). NO ssl field.
```svelte
<script>
  import { onMount } from 'svelte'
  let { store } = $props()
  let enabled = $state(true), type = $state('redis'), host = $state(''), port = $state(''), ttl = $state('')
  onMount(async () => { if (!store.config) await store.load(); sync() })
  function sync() {
    const ls = store.config?.litellm_settings ?? {}; const cp = ls.cache_params ?? {}
    enabled = ls.cache ?? false; type = cp.type ?? 'redis'; host = cp.host ?? ''; port = cp.port ?? ''; ttl = cp.ttl ?? ''
  }
  async function save() {
    const ls = { ...(store.config?.litellm_settings ?? {}) }
    ls.cache = enabled
    const cp = { ...(ls.cache_params ?? {}), type, host, port }
    if (ttl !== '' && ttl != null) cp.ttl = Number(ttl); else delete cp.ttl
    delete cp.ssl; delete cp.ssl_check_hostname   // guardrail: never emit ssl
    ls.cache_params = cp
    const ok = await store.saveSection('litellm_settings', ls); if (ok) sync()
  }
</script>
<div class="page"><h1>Caching</h1>
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}
  {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}
  <div class="card">
    <label class="row"><input type="checkbox" bind:checked={enabled} /> Enable response cache (Valkey/Redis)</label>
    <label>Type <input bind:value={type} placeholder="redis" /></label>
    <label>Host <input bind:value={host} placeholder="os.environ/REDIS_HOST" /></label>
    <label>Port <input bind:value={port} placeholder="os.environ/REDIS_PORT" /></label>
    <label>TTL (seconds) <input type="number" min="0" bind:value={ttl} placeholder="default 600" /></label>
    <p class="hint">The UI never writes an <code>ssl</code> key — LiteLLM bug #10949 makes any ssl key hang against plain Valkey.</p>
    <div class="row"><button class="primary" onclick={save} disabled={store.applying}>Save &amp; apply</button><button onclick={sync} disabled={store.applying}>Reset</button></div>
  </div>
</div>
<style>
  .page{padding:24px 30px;max-width:680px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card);display:flex;flex-direction:column;gap:12px}
  label{display:flex;flex-direction:column;font-size:13px;color:var(--muted);gap:4px}label.row{flex-direction:row;align-items:center;gap:8px;color:var(--text)}
  input{padding:8px;border:1px solid var(--border);border-radius:8px;font:inherit;background:var(--card);color:var(--text)}
  .row{display:flex;gap:8px}button{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}button:disabled{opacity:.5}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px}.banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:var(--muted)}
</style>
```

- [ ] **Step 3: `Housekeeping.svelte`** — DB stats + Run-now + cron status:
```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let d = $state(null), err = $state(''), busy = $state(false), result = $state(null)
  async function load() { try { d = await api.housekeeping() } catch (e) { err = e.message } }
  onMount(load)
  async function run() {
    if (!confirm('Run maintenance now? This deletes spend logs older than the retention window and expired keys.')) return
    busy = true; err = ''; result = null
    try { result = await api.runHousekeeping(); await load() } catch (e) { err = e.message } finally { busy = false }
  }
</script>
<div class="page"><h1>DB Housekeeping</h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if d}
    <div class="card"><h2>Database</h2>
      <p>Size: <strong>{d.stats.db_size}</strong></p>
      <table><tbody>{#each Object.entries(d.stats.row_counts) as [t, n]}<tr><td>{t}</td><td>{n ?? '—'} rows</td></tr>{/each}</tbody></table>
    </div>
    <div class="card"><h2>Maintenance</h2>
      <p>Scheduled cron: <strong>{d.settings.enabled ? `every ${d.settings.interval_hours}h` : 'disabled'}</strong>
        · retention <strong>{d.settings.retention_days} days</strong>
        · delete expired keys: <strong>{d.settings.delete_expired_keys ? 'yes' : 'no'}</strong></p>
      <p class="hint">Enable/tune the cron via <code>HOUSEKEEPING_*</code> env vars. "Run now" applies the same retention immediately.</p>
      <button class="danger" onclick={run} disabled={busy}>{busy ? 'Running…' : 'Run maintenance now'}</button>
      {#if result}<div class="banner ok">Trimmed {result.trimmed_spend_logs} spend logs{result.deleted_expired_keys != null ? `, deleted ${result.deleted_expired_keys} expired keys` : ''} (retention {result.retention_days}d).</div>{/if}
    </div>
  {/if}
</div>
<style>
  .page{padding:24px 30px;max-width:760px}h2{font-size:15px;margin:0 0 10px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card)}
  table{width:100%;border-collapse:collapse}td{padding:6px 8px;border-bottom:1px solid var(--border);font-size:14px}
  button{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}
  button.danger{color:#ff3b30;border-color:#ffd0cc}button:disabled{opacity:.5}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px;margin-top:10px}.banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}
  .hint{font-size:12px;color:var(--muted)}
</style>
```

- [ ] **Step 4: `Settings.svelte`** — export/import + dark mode toggle:
```svelte
<script>
  import { api } from '../lib/api.js'
  let { store, theme, setTheme } = $props()
  let importErr = $state(''), importMsg = $state('')
  async function onImport(e) {
    const file = e.target.files?.[0]; if (!file) return
    importErr = ''; importMsg = ''
    let cfg
    try { cfg = JSON.parse(await file.text()) }
    catch { try { const YAML = await import('yaml'); cfg = YAML.parse(await file.text()) } catch (er) { importErr = 'File must be JSON or YAML'; return } }
    if (!store.config) await store.load()
    const ok = await store.saveSection_full ? null : null
    // import replaces the whole config via PUT (full object)
    try { await api.putConfig(cfg); importMsg = 'Imported & applied.'; await store.load() }
    catch (er) { importErr = (er.status === 422 ? 'Rejected: ' : er.status === 409 ? 'Reload failed, rolled back: ' : '') + er.message }
  }
</script>
<div class="page"><h1>Settings</h1>
  <div class="card"><h2>Appearance</h2>
    <label class="row"><input type="checkbox" checked={theme==='dark'} onchange={(e) => setTheme(e.target.checked ? 'dark' : 'light')} /> Dark mode</label>
  </div>
  <div class="card"><h2>Export / Import config</h2>
    <p class="hint">Download a snapshot of <code>config.yaml</code>, or import one (validated + applied via safe-apply).</p>
    <div class="row">
      <a class="btn" href={api.exportConfigUrl} download>⬇ Export config.yaml</a>
      <label class="btn">⬆ Import…<input type="file" accept=".yaml,.yml,.json" onchange={onImport} style="display:none" /></label>
    </div>
    {#if importErr}<div class="banner err">{importErr}</div>{/if}
    {#if importMsg}<div class="banner ok">{importMsg}</div>{/if}
    {#if store.applying}<div class="banner info">Applying… restarting the proxy (~25s)</div>{/if}
  </div>
</div>
<style>
  .page{padding:24px 30px;max-width:680px}h2{font-size:15px;margin:0 0 10px}
  .card{border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:14px;background:var(--card)}
  label.row{display:flex;align-items:center;gap:8px;color:var(--text)}
  .row{display:flex;gap:10px;align-items:center}
  .btn{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);cursor:pointer;text-decoration:none;font-size:14px}
  .banner{padding:10px 12px;border-radius:8px;font-size:13px;margin-top:10px}.banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}.banner.info{background:#eef4ff;color:#0a52c7}
  .hint{font-size:12px;color:var(--muted)}
</style>
```
(Note: remove the dead `saveSection_full` line — import uses `api.putConfig(cfg)` directly. The `yaml` dynamic import is optional; JSON import always works. If the `yaml` package isn't bundled, keep JSON-only and adjust the accept hint.)

- [ ] **Step 5: `App.svelte`** — theme state + CSS variables + nav. In `<script>`:
```javascript
  let theme = $state(localStorage.getItem('theme') || 'light')
  $effect(() => { document.documentElement.setAttribute('data-theme', theme); localStorage.setItem('theme', theme) })
  function setTheme(t) { theme = t }
  import Caching from './routes/Caching.svelte'
  import Housekeeping from './routes/Housekeeping.svelte'
  import Settings from './routes/Settings.svelte'
```
Add nav: under Configuration add `<button class="nav" class:active={screen==='caching'} onclick={() => screen='caching'}>⚡ Caching</button>`; add a new group `<div class="navgroup">System</div>` with `Housekeeping` (`🧹`) and `Settings` (`⚙︎`). Render branches: `{:else if screen==='caching'}<Caching {store} />{:else if screen==='housekeeping'}<Housekeeping />{:else if screen==='settings'}<Settings {store} {theme} {setTheme} />`.
Add the theme variables to the `:global` style block:
```css
  :global(:root){--bg:#fff;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--border:rgba(0,0,0,.08);--sidebar:#f5f5f7}
  :global([data-theme="dark"]){--bg:#1c1c1e;--card:#2c2c2e;--text:#f5f5f7;--muted:#98989d;--border:rgba(255,255,255,.12);--sidebar:#161618}
  :global(body){background:var(--bg);color:var(--text)}
```
Update the shell `.sidebar`/`.main` to use `background:var(--sidebar)`/`var(--bg)` and `.nav` colors to `var(--text)`. (Older inline-styled screens — Login/Dashboard/ConfigViewer — may stay light; the main shell + Phase 2–5 screens adopt the theme.)

- [ ] **Step 6: build** `cd ui/frontend && npm run build` → success. Commit `feat(ui): Caching, Housekeeping, Settings (export/import + dark mode) + nav`.

---

## Task 6: Real-stack integration verification

- [ ] **Step 1:** build + up; log in. **Caching:** set TTL 300 → Save & apply → config.yaml `cache_params.ttl: 300`, NO `ssl` key, proxy healthy.
- [ ] **Step 2:** **Housekeeping:** open it → DB size + row counts render; "Run maintenance now" → returns trimmed/deleted counts (0 on a fresh DB), no error; `/api/housekeeping` returns 200.
- [ ] **Step 3:** **Settings:** toggle Dark mode (UI recolors, persists on reload); Export downloads config.yaml; Import a small edited config (e.g. JSON with routing_strategy least-busy) → applied (or 422 if invalid).
- [ ] **Step 4:** Tear down + restore config.yaml.

## Self-Review
- **Spec coverage:** Caching editor (no ssl) ✓; DB housekeeping — built-in retention (general_settings via safe-apply) + UI cron (APScheduler, opt-in) + stats + manual run ✓; Export/Import ✓; Dark mode ✓.
- **Safety:** maintenance is opt-in (cron off by default), retention-bounded, parameterized SQL (`make_interval(days => $1)`), manual run requires a confirm + returns counts; `/api/housekeeping` 503 if no DATABASE_URL, 502 on DB error.
- **Guardrails:** Caching strips `ssl`/`ssl_check_hostname` client-side AND the backend rejects them; import goes through the validated `PUT /api/config`.

## Follow-on
Docs: README screenshots (all screens) + a clear **credit to the LiteLLM proxy project (BerriAI/litellm)**; per-phase doc updates in `docs/admin-ui.md`; note the host-editability (`config.yaml` is UI-owned `0644`) tradeoff.
