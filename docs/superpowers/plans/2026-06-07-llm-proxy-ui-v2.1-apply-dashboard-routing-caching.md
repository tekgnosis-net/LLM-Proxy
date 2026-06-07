# LLM-Proxy Admin UI — v2.1 (Apply model + Dashboard + Routing + Caching) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Backend = TDD. Frontend (Svelte 5) = build + real-stack verification. Steps use `- [ ]`.

**Goal:** Decouple editing from applying — per-setting **Save** stages changes to `config.yaml` (no restart); a global **Apply** bar restarts once and rolls back to a known-good baseline on failure. Plus the prototype Dashboard, routing timeout/cooldown fields, and a read-only Caching panel.

**Architecture:** `config_store.write_config` already validates+writes+backs-up without reloading — that becomes "Save". A new `apply_config` uses a `config/.applied.yaml` baseline (last successfully-applied config) to restart→verify→rollback. `pending_status` compares `config.yaml` vs the baseline (semantic). `PUT /api/config`=save, `POST /api/apply`=apply, `GET /api/apply/status`=pending.

**Tech Stack:** FastAPI, Svelte 5. No new deps.

**Spec:** [`../specs/2026-06-07-llm-proxy-ui-v2-design.md`](../specs/2026-06-07-llm-proxy-ui-v2-design.md) (§ Save→Apply, Phase v2.1).

---

## File Structure
```
ui/app/config_store.py        # MODIFY: + pending_status(), .applied.yaml helpers (write_config stays = "save")
ui/app/apply.py               # CREATE: apply_config() (baseline restart+verify+rollback) + ApplyError
ui/app/safe_apply.py          # (kept for reference; routes stop using it)
ui/app/routes/config_routes.py# MODIFY: PUT=save-only; + POST /api/apply, GET /api/apply/status
ui/app/settings.py            # MODIFY: + redis_host/redis_port (caching display)
docker-compose.yml            # MODIFY: pass REDIS_HOST/REDIS_PORT to llm-proxy-ui; gitignore .applied.yaml
.gitignore                    # MODIFY: config/.applied.yaml
ui/tests/test_apply.py        # CREATE
ui/tests/test_config_store.py # MODIFY: pending_status tests
ui/tests/test_config_routes.py# MODIFY: PUT save-only + apply + status
ui/frontend/src/lib/configStore.svelte.js  # MODIFY: save (no wait) + pending + apply()
ui/frontend/src/lib/api.js    # MODIFY: + applyStatus(), apply()
ui/frontend/src/App.svelte    # MODIFY: global Apply bar
ui/frontend/src/routes/Dashboard.svelte  # REWRITE: KPI cards
ui/frontend/src/routes/Routing.svelte    # MODIFY: + timeout/cooldown/allowed_fails/retry_after
ui/frontend/src/routes/Caching.svelte    # REWRITE: read-only panel
```

---

## Task 1: config_store — `pending_status` + baseline helpers (TDD)

**Files:** Modify `ui/app/config_store.py`; Test `ui/tests/test_config_store.py`.

- [ ] **Step 1: failing tests** (append):
```python
from pathlib import Path
from app.config_store import write_config, pending_status, APPLIED_SUFFIX


def _seed(tmp_path, routing="simple-shuffle"):
    p = str(tmp_path / "config.yaml")
    write_config(p, {"router_settings": {"routing_strategy": routing}, "model_list": []})
    return p


def test_pending_false_when_no_baseline_seeds_it(tmp_path):
    p = _seed(tmp_path)
    st = pending_status(p)
    assert st["pending"] is False
    assert (tmp_path / ".applied.yaml").exists()   # baseline seeded from current


def test_pending_true_after_edit(tmp_path):
    p = _seed(tmp_path)
    pending_status(p)                                # seed baseline
    write_config(p, {"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    st = pending_status(p)
    assert st["pending"] is True
    assert "router_settings" in st["summary"]


def test_pending_false_when_identical(tmp_path):
    p = _seed(tmp_path)
    pending_status(p)
    write_config(p, {"router_settings": {"routing_strategy": "simple-shuffle"}, "model_list": []})  # same content
    assert pending_status(p)["pending"] is False
```

- [ ] **Step 2: run red** — `cd ui && .venv/bin/python -m pytest tests/test_config_store.py -k pending -v` → FAIL (`pending_status`/`APPLIED_SUFFIX` missing).

- [ ] **Step 3: implement** in `ui/app/config_store.py`:
```python
APPLIED_SUFFIX = ".applied.yaml"


def _applied_path(config_path: str) -> Path:
    return Path(config_path).parent / APPLIED_SUFFIX


def seed_baseline_if_missing(config_path: str) -> None:
    """Seed the baseline from the current config (fresh deploy / first run)."""
    applied = _applied_path(config_path)
    cur = Path(config_path)
    if not applied.exists() and cur.exists():
        applied.write_text(cur.read_text())
        os.chmod(applied, 0o644)


def promote_baseline(config_path: str) -> None:
    """Mark the current config as the applied baseline (call after a successful apply)."""
    applied = _applied_path(config_path)
    applied.write_text(Path(config_path).read_text())
    os.chmod(applied, 0o644)


def restore_baseline(config_path: str) -> None:
    """Restore config.yaml from the applied baseline (rollback)."""
    applied = _applied_path(config_path)
    if applied.exists():
        Path(config_path).write_text(applied.read_text())
        os.chmod(config_path, 0o644)


def pending_status(config_path: str) -> dict:
    """Compare current config to the applied baseline (semantic). Seeds baseline if missing."""
    applied = _applied_path(config_path)
    if not applied.exists():
        seed_baseline_if_missing(config_path)
        return {"pending": False, "summary": []}
    try:
        cur = load_config(config_path).model_dump(exclude_none=True)
        base = load_config(str(applied)).model_dump(exclude_none=True)
    except ConfigError:
        return {"pending": True, "summary": ["(unparseable config)"]}
    if cur == base:
        return {"pending": False, "summary": []}
    keys = sorted(set(cur) | set(base))
    return {"pending": True, "summary": [k for k in keys if cur.get(k) != base.get(k)]}
```
(`os` is already imported in config_store.)

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): config baseline + pending_status (staged-save support)`.

---

## Task 2: `apply.py` — baseline restart + verify + rollback (TDD)

**Files:** Create `ui/app/apply.py`, `ui/tests/test_apply.py`.

- [ ] **Step 1: failing tests** (`ui/tests/test_apply.py`):
```python
import pytest
from pathlib import Path
from app.config_store import write_config, pending_status, load_config
from app.apply import apply_config, ApplyError


class FakeReloader:
    def __init__(self, ok=True): self.ok = ok; self.calls = 0
    async def reload_and_verify(self, expected_models):
        self.calls += 1
        if not self.ok:
            from app.reloader import ReloadError
            raise ReloadError("sim")
        return True


def _cfg(tmp_path, routing):
    p = str(tmp_path / "config.yaml")
    write_config(p, {"router_settings": {"routing_strategy": routing}, "model_list": []})
    return p


@pytest.mark.asyncio
async def test_apply_promotes_baseline_and_clears_pending(tmp_path):
    p = _cfg(tmp_path, "simple-shuffle")
    pending_status(p)                                    # seed baseline
    write_config(p, {"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    assert pending_status(p)["pending"] is True
    await apply_config(p, FakeReloader(ok=True))
    assert pending_status(p)["pending"] is False         # baseline promoted
    assert load_config(p).router_settings.routing_strategy == "least-busy"


@pytest.mark.asyncio
async def test_apply_rolls_back_on_reload_failure(tmp_path):
    p = _cfg(tmp_path, "simple-shuffle")
    pending_status(p)
    write_config(p, {"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    rl = FakeReloader(ok=False)
    with pytest.raises(ApplyError):
        await apply_config(p, rl)
    assert load_config(p).router_settings.routing_strategy == "simple-shuffle"  # restored
    assert rl.calls == 2                                  # apply attempt + rollback reload
```

- [ ] **Step 2: run red** → FAIL (`app.apply` missing).

- [ ] **Step 3: implement `ui/app/apply.py`:**
```python
from __future__ import annotations
from pathlib import Path
from app.config_store import (load_config, ConfigError, ProxyConfig,
                              seed_baseline_if_missing, promote_baseline, restore_baseline)
from app.reloader import ReloadError


class ApplyError(RuntimeError):
    pass


def _expected(cfg: ProxyConfig) -> list[str]:
    return [m.model_name for m in cfg.model_list]


async def apply_config(config_path: str, reloader) -> dict:
    """Restart the proxy onto the staged config.yaml and verify. On failure, restore
    the last-applied baseline and restart back onto it. On success, promote the
    current config to the baseline."""
    seed_baseline_if_missing(config_path)
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        raise ApplyError(f"config invalid, not applied: {e}") from e
    try:
        await reloader.reload_and_verify(_expected(cfg))
        promote_baseline(config_path)
        return {"applied": True, "models": _expected(cfg),
                "routing_strategy": cfg.router_settings.routing_strategy}
    except ReloadError as e:
        restore_baseline(config_path)
        try:
            await reloader.reload_and_verify(_expected(load_config(config_path)))
        except Exception:
            pass   # best-effort; restart:unless-stopped recovers the file-backed baseline
        raise ApplyError(f"reload failed; rolled back to last applied config: {e}") from e
```

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): apply_config with baseline rollback`.

---

## Task 3: routes — PUT save-only + `/api/apply` + `/api/apply/status` (TDD)

**Files:** Modify `ui/app/routes/config_routes.py`, `ui/tests/test_config_routes.py`.

- [ ] **Step 1: update/extend tests** — replace the v1 PUT-applies/409 tests with save-only + apply + status:
```python
def test_put_config_saves_without_apply(tmp_path):
    c = _client(tmp_path)            # existing helper (logged in)
    r = c.put("/api/config", json={"router_settings": {"routing_strategy": "simple-shuffle"}, "model_list": []})
    assert r.status_code == 200 and r.json()["pending"] is True
    assert c.get("/api/apply/status").json()["pending"] is True   # staged, not applied


def test_put_config_invalid_422(tmp_path):
    c = _client(tmp_path)
    assert c.put("/api/config", json={"router_settings": {"routing_strategy": "lowest-cost"}}).status_code == 422


def test_apply_ok(tmp_path):
    c = _client(tmp_path, reloader_ok=True)
    c.put("/api/config", json={"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    assert c.post("/api/apply").status_code == 200
    assert c.get("/api/apply/status").json()["pending"] is False


def test_apply_rollback_409(tmp_path):
    c = _client(tmp_path, reloader_ok=False)
    c.put("/api/config", json={"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    assert c.post("/api/apply").status_code == 409


def test_apply_requires_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.post("/api/apply").status_code == 401
```
The `_client` helper: keep the `make_reloader` seam (now consumed by the apply route). Ensure a fresh baseline per test (tmp_path config) — the first `pending_status`/apply seeds it.

- [ ] **Step 2: run red** → FAIL.

- [ ] **Step 3: implement** — change PUT to save-only, add apply + status. In `config_routes.py`:
```python
from app.config_store import write_config, pending_status  # (load_config already imported)
from app.apply import apply_config, ApplyError

@router.put("/config", dependencies=[Depends(login_required)])
def put_config(raw: dict = Body(...)):
    s = get_settings()
    try:
        write_config(s.config_path, raw)        # validate + stage (NO restart)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, **pending_status(s.config_path)}

@router.get("/apply/status", dependencies=[Depends(login_required)])
def apply_status():
    return pending_status(get_settings().config_path)

@router.post("/apply", dependencies=[Depends(login_required)])
async def apply():
    s = get_settings()
    try:
        return await apply_config(s.config_path, make_reloader())
    except ApplyError as e:
        code = 422 if "invalid" in str(e) else 409
        raise HTTPException(status_code=code, detail=str(e))
```
Remove the old `safe_apply` import/usage from PUT (the file may keep `make_reloader`).

- [ ] **Step 4: green + full suite.** **Step 5: commit** `feat(ui): PUT=save-only, POST /api/apply, GET /api/apply/status`.

---

## Task 4: settings + compose — Redis display values + gitignore baseline

**Files:** Modify `ui/app/settings.py`, `docker-compose.yml`, `.gitignore`.

- [ ] **Step 1:** `settings.py` add (for the read-only caching panel):
```python
    redis_host: str = ""    # display only (resolved from compose), e.g. "valkey"
    redis_port: str = ""    # display only, e.g. "6379"
```
- [ ] **Step 2:** `docker-compose.yml` `llm-proxy-ui` env — add:
```yaml
      REDIS_HOST: ${REDIS_HOST:-valkey}
      REDIS_PORT: ${REDIS_PORT:-6379}
```
- [ ] **Step 3:** `.gitignore` (root) add: `config/.applied.yaml`
- [ ] **Step 4:** `ADMIN_PASSWORD_HASH=x SESSION_SECRET=y docker compose config -q && echo OK`; full suite green. Commit `feat(ui): expose redis host/port for caching display; ignore .applied.yaml`.

---

## Task 5: frontend — configStore save/pending/apply + global Apply bar

**Files:** Modify `ui/frontend/src/lib/api.js`, `configStore.svelte.js`, `App.svelte`.

- [ ] **Step 1: api.js** — add:
```javascript
  applyStatus: () => req('/api/apply/status'),
  apply: () => req('/api/apply', { method: 'POST' }),
```
(`putConfig` stays; it's now save-only server-side.)

- [ ] **Step 2: configStore.svelte.js** — saves no longer block on a restart; add pending + apply:
```javascript
import { api } from './api.js'

export function createConfigStore() {
  let config = $state(null), loading = $state(false), saving = $state(false)
  let applying = $state(false), error = $state(''), notice = $state('')
  let pending = $state(false), pendingSummary = $state([])

  async function refreshPending() {
    try { const s = await api.applyStatus(); pending = s.pending; pendingSummary = s.summary || [] } catch {}
  }
  async function load() {
    loading = true; error = ''
    try { config = await api.config(); await refreshPending() } catch (e) { error = e.message } finally { loading = false }
  }
  async function saveSection(section, value) {
    if (!config) return false
    const candidate = { ...config, [section]: value }
    saving = true; error = ''; notice = ''
    try {
      const res = await api.putConfig(candidate)   // save only (fast)
      config = candidate; pending = res.pending; pendingSummary = res.summary || []
      notice = 'Saved. Click Apply to restart the proxy and make it live.'
      return true
    } catch (e) { error = e.status === 422 ? `Rejected: ${e.message}` : e.message; return false }
    finally { saving = false }
  }
  async function apply() {
    applying = true; error = ''; notice = ''
    try {
      const res = await api.apply()
      notice = `Applied — ${(res.models||[]).length} model(s), routing ${res.routing_strategy||'—'}`
      await refreshPending(); return true
    } catch (e) {
      error = e.status === 409 ? `Reload failed — rolled back: ${e.message}` : e.message
      await refreshPending(); return false
    } finally { applying = false }
  }
  return {
    get config(){return config}, get loading(){return loading}, get saving(){return saving},
    get applying(){return applying}, get error(){return error}, get notice(){return notice},
    get pending(){return pending}, get pendingSummary(){return pendingSummary},
    load, saveSection, apply, refreshPending,
  }
}
```

- [ ] **Step 3: App.svelte** — render a global Apply bar (top-left of main) when `store.pending`. In the script, ensure the shared `store` exists (it does) and call `store.refreshPending()` on mount. Add at the top of `<main>`:
```svelte
    {#if store.pending}
      <div class="applybar">
        <span><strong>{store.pendingSummary.length || ''}</strong> unapplied change{store.pendingSummary.length === 1 ? '' : 's'}{store.pendingSummary.length ? ` (${store.pendingSummary.join(', ')})` : ''}</span>
        <button class="apply" onclick={() => store.apply()} disabled={store.applying}>{store.applying ? 'Applying… (~25s)' : 'Apply'}</button>
      </div>
    {/if}
```
Styles:
```css
  .applybar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;justify-content:space-between;
    background:#fff7e6;border-bottom:1px solid #ffe1a8;padding:8px 16px;font-size:13px;color:#7a5b00}
  .applybar .apply{background:#ff9f0a;color:#fff;border:0;border-radius:8px;padding:6px 14px;font-weight:600;cursor:pointer}
  .applybar .apply:disabled{opacity:.6}
```
On mount: `onMount(() => { ... ; store.refreshPending() })`.

- [ ] **Step 4: build** `cd ui/frontend && npm run build` → success. Commit `feat(ui): staged-save store + global Apply bar`.

---

## Task 6: frontend — Dashboard rebuild (KPI cards)

**Files:** Rewrite `ui/frontend/src/routes/Dashboard.svelte`.

- [ ] **Step 1: implement** — KPI cards from existing endpoints:
```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let health = $state(null), usage = $state(null), keys = $state(null), cfg = $state(null), err = $state('')
  onMount(async () => {
    try {
      ;[health, cfg] = await Promise.all([api.health(), api.config().catch(() => null)])
      usage = await api.usage().catch(() => null)
      keys = await api.keys().catch(() => null)
    } catch (e) { err = e.message }
  })
  const dot = (ok) => ok ? '#34c759' : '#ff3b30'
  function modelCount() { return cfg?.model_list?.length ?? '—' }
  function keyCount() { return Array.isArray(keys) ? keys.length : '—' }
  function spend() { return usage?.total?.spend != null ? `$${Number(usage.total.spend).toFixed(2)}` : '$0.00' }
  function cacheOn() { return cfg?.litellm_settings?.cache ? 'on' : 'off' }
</script>
<div class="page">
  <h1>Dashboard</h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  <div class="cards">
    <div class="card"><div class="lbl">Proxy</div>
      <div class="big"><span class="d" style="background:{dot(health?.proxy?.reachable)}"></span>{health?.proxy?.reachable ? 'Healthy' : 'Down'}</div>
      <div class="sub">{health?.proxy?.raw?.db === 'connected' ? 'DB connected' : '—'}</div></div>
    <div class="card"><div class="lbl">Models</div><div class="big">{modelCount()}</div><div class="sub">in config.yaml</div></div>
    <div class="card"><div class="lbl">Virtual keys</div><div class="big">{keyCount()}</div><div class="sub">active</div></div>
    <div class="card"><div class="lbl">Spend (30d)</div><div class="big">{spend()}</div><div class="sub">all keys</div></div>
    <div class="card"><div class="lbl">Cache</div><div class="big">{cacheOn()}</div><div class="sub">{cfg?.litellm_settings?.cache_params?.type ?? '—'}</div></div>
  </div>
</div>
<style>
  .page{padding:24px 30px;max-width:1000px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-top:16px}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;background:var(--card,#fff)}
  .lbl{font-size:12px;color:var(--muted,#6e6e73)}.big{font-size:26px;font-weight:600;margin-top:6px;display:flex;align-items:center;gap:8px}
  .sub{font-size:12px;color:var(--muted,#6e6e73);margin-top:4px}
  .d{width:10px;height:10px;border-radius:50%;display:inline-block}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;margin-top:12px;font-size:13px}
</style>
```

- [ ] **Step 2: build** + commit `feat(ui): dashboard rebuild with KPI cards`.

---

## Task 7: frontend — Routing timeout/cooldown/allowed_fails/retry_after

**Files:** Modify `ui/frontend/src/routes/Routing.svelte`.

- [ ] **Step 1:** add four numeric fields bound into `router_settings` on save. In the `<script>`, extend `sync()` and `save()`:
```javascript
  let timeout = $state(''), cooldown = $state(''), allowedFails = $state(''), retryAfter = $state('')
  // in sync(): read rs.timeout / rs.cooldown_time / rs.allowed_fails / rs.retry_after into the above
  // in save(): const n=(v)=> v===''||v==null?undefined:Number(v); then set on rs:
  //   ['timeout','cooldown_time','allowed_fails','retry_after'] from [timeout,cooldown,allowedFails,retryAfter]
  //   deleting the key when undefined (mirror the num_retries handling)
```
Add the inputs in the card (after num_retries):
```svelte
    <label>Timeout (s) <input type="number" min="0" step="0.1" bind:value={timeout} placeholder="default 600" /></label>
    <label>Cooldown time (s) <input type="number" min="0" bind:value={cooldown} placeholder="after allowed_fails" /></label>
    <label>Allowed fails <input type="number" min="0" bind:value={allowedFails} placeholder="per minute before cooldown" /></label>
    <label>Retry after (s) <input type="number" min="0" bind:value={retryAfter} placeholder="min before retry" /></label>
```
Keep the button label **Save** (apply is now global). Implement the exact `sync`/`save` wiring (full code) mirroring the existing `strategy`/`numRetries` pattern.

- [ ] **Step 2: build** + commit `feat(ui): routing timeout/cooldown/allowed_fails/retry_after`.

---

## Task 8: frontend — Caching read-only panel

**Files:** Rewrite `ui/frontend/src/routes/Caching.svelte`; expose redis values via a tiny endpoint or config.

- [ ] **Step 1: backend** — add `GET /api/cache/info` (login-gated) returning effective values:
In `config_routes.py`:
```python
@router.get("/cache/info", dependencies=[Depends(login_required)])
def cache_info():
    s = get_settings()
    cfg = load_config(s.config_path)
    cp = cfg.litellm_settings.cache_params
    return {"enabled": bool(cfg.litellm_settings.cache),
            "type": getattr(cp, "type", None) if cp else None,
            "ttl": getattr(cp, "ttl", None) if cp else None,
            "host": s.redis_host or "valkey", "port": s.redis_port or "6379"}
```
Add a quick test in `test_config_routes.py` (login-gated; returns host/port). api.js: `cacheInfo: () => req('/api/cache/info')`.

- [ ] **Step 2: `Caching.svelte`** (read-only):
```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  let info = $state(null), err = $state('')
  onMount(async () => { try { info = await api.cacheInfo() } catch (e) { err = e.message } })
</script>
<div class="page"><h1>Caching <span class="sub">read-only</span></h1>
  {#if err}<div class="banner err">{err}</div>{/if}
  {#if info}
    <div class="card">
      <div class="row"><span>Status</span><strong>{info.enabled ? 'Enabled' : 'Disabled'}</strong></div>
      <div class="row"><span>Type</span><strong>{info.type || '—'}</strong></div>
      <div class="row"><span>Backend</span><strong>{info.host} : {info.port}</strong></div>
      <div class="row"><span>TTL</span><strong>{info.ttl != null ? info.ttl + ' s' : 'default (600 s)'}</strong></div>
      <p class="hint">The cache backend is provisioned in <code>docker-compose.yml</code> (the <code>valkey</code>
      service, reached via Docker DNS at <code>{info.host}:{info.port}</code>). To change it, edit
      <code>docker-compose.yml</code> / <code>config.yaml</code> — it isn't editable here by design.</p>
    </div>
  {/if}
</div>
<style>
  .page{padding:24px 30px;max-width:620px}.sub{font-size:13px;color:var(--muted,#6e6e73);font-weight:400}
  .card{border:1px solid var(--border,rgba(0,0,0,.08));border-radius:12px;padding:16px;margin-top:14px;background:var(--card,#fff)}
  .row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border,rgba(0,0,0,.06));font-size:14px}
  .hint{font-size:12px;color:var(--muted,#6e6e73);margin-top:12px}
  .banner.err{background:#ffeceb;color:#c0271d;padding:10px 12px;border-radius:8px;font-size:13px}
</style>
```
The Caching nav button no longer needs the `store`; it self-loads. (App.svelte: `<Caching />` without `{store}`.)

- [ ] **Step 3: build** + commit `feat(ui): read-only caching status panel`.

---

## Task 9: real-stack integration verification

- [ ] **Step 1:** build + up; log in. Make two edits (Routing strategy + a Models change later, or two routing fields) saving each → the **Apply bar** shows "2 unapplied changes". `GET /api/apply/status` → pending true.
- [ ] **Step 2:** Click **Apply** → one restart (~25s) → bar clears; `config.yaml` shows the changes; `.applied.yaml` matches.
- [ ] **Step 3:** Stage a config that breaks reload (bad cache type via direct PUT) → Apply → 409, bar still pending, proxy healthy on the rolled-back config (`.applied.yaml`), `config.yaml` restored.
- [ ] **Step 4:** Dashboard shows KPI cards; Caching shows `valkey:6379` read-only; Routing shows the new fields. Tear down; restore config.

## Self-Review
- **Spec coverage:** staged save (T1,T3,T5) ✓; baseline apply+rollback (T2,T3) ✓; pending bar (T5) ✓; dashboard (T6) ✓; routing fields (T7) ✓; read-only caching with effective host/port (T4,T8) ✓.
- **Placeholders:** Task 7's `sync`/`save` wiring is described as "mirror the existing pattern" with the exact fields/inputs given — the implementer extends the known `num_retries` code; acceptable (the pattern is fully shown in the existing file + the field list is explicit).
- **Type consistency:** `write_config`/`pending_status`/`seed_baseline_if_missing`/`promote_baseline`/`restore_baseline` (config_store), `apply_config`/`ApplyError` (apply), `make_reloader` seam, `api.{applyStatus,apply,cacheInfo}`, store `{saveSection,apply,pending,refreshPending}` consistent across tasks.

## Follow-on
v2.2 (provider keys + models v2), v2.3 (catalog syncs). Executed together as one phased goal.
