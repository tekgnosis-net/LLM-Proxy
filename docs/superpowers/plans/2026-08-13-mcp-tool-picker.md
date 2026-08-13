# MCP Allowed-Tools Fetched Picker (v3.28) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Fetch tools" on the MCP Servers form — probe the MCP server at the URL/auth currently entered (pre-save) and turn Allowed tools into a checkbox picker.

**Architecture:** New pure probe module (`mcp_probe.py`, httpx JSON-RPC over streamable HTTP) + one new admin endpoint (`POST /api/mcp/tools/preview`, with stored-secret decrypt parity) + a form-local picker in McpServers.svelte backed by a pure merge helper in lib/mcp.js.

**Tech Stack:** FastAPI + httpx (backend), Svelte 5 runes (frontend), pytest + httpx.MockTransport, node module asserts.

**Spec:** `docs/superpowers/specs/2026-08-13-mcp-tool-picker-design.md`. **Branch:** `v3.28-mcp-tool-picker`.

## Global Constraints

- Auth header mapping EXACT (LiteLLM `experimental_mcp_client/client.py:351-376`): `bearer_token` → `Authorization: Bearer <v>`; `basic` → `Authorization: Basic <v>` (value verbatim, NO base64); `api_key` → `X-API-Key: <v>`.
- JSON-RPC probe sequence: `initialize` (protocolVersion `2025-03-26`, headers `Accept: application/json, text/event-stream` + `Content-Type: application/json`) → optional `mcp-session-id` echo → `notifications/initialized` (expect 202) → `tools/list`. Responses SSE-framed (first `data:` line) OR plain JSON — both parsed.
- Direct preview: `transport == "http"` only; everything else → ProbeError with the friendly message.
- Plaintext `auth_value` never in responses, logs, or error text; httpx errors surfaced by CLASS NAME only (URLs can embed keys).
- `allowed_tools` semantics unchanged (empty = all; enforcement LiteLLM-side); no apply-pipeline/schema changes.
- Tests: `cd /home/kumar/workspace/litellm/ui && .venv/bin/python -m pytest tests/ -q` (baseline 362 passed, 1 skipped — keep green). Build: `cd ui/frontend && npm run build`. Node asserts via `node --input-type=module -e` from `ui/frontend`.
- Commits: conventional + trailer `Claude-Session: https://claude.ai/code/session_011o3rL25n2rCTBGrXP5uPYE`; NO AI attribution. `git add` ONLY changed files; the untracked `docs/superpowers/specs/2026-07-13-config-integrity-phase1-design.md` must never be committed. Never commit `.env`.

## File Structure

| File | Change |
|---|---|
| `ui/app/mcp_probe.py` | NEW — probe + header builder + frame parser |
| `ui/app/routes/mcp_routes.py` | `POST /api/mcp/tools/preview` |
| `ui/frontend/src/lib/mcp.js` | `mergeToolChoices` |
| `ui/frontend/src/lib/api.js` | `mcpToolsPreview` |
| `ui/frontend/src/routes/McpServers.svelte` | Fetch-tools picker in the Allowed tools section |
| `ui/tests/test_mcp_probe.py` | NEW |
| `ui/tests/test_mcp_routes.py` | extended |
| `docs/mcp-gateway.md` | Fetch-tools paragraph |

---

### Task 1: `mcp_probe.py`

**Files:** Create `ui/app/mcp_probe.py`; Test `ui/tests/test_mcp_probe.py`.

**Interfaces:**
- Produces (used by Task 2): `ProbeError(RuntimeError)`; `build_probe_headers(auth_type, auth_value, static_headers) -> dict`; `parse_rpc_response(r: httpx.Response) -> dict | None`; `async probe_tools(url, auth_type=None, auth_value=None, static_headers=None, transport="http", timeout=10.0, http_transport=None) -> list[{"name","description"}]`.

- [ ] **Step 1: Failing tests** — `ui/tests/test_mcp_probe.py`:

```python
import json, httpx, pytest
from app.mcp_probe import ProbeError, build_probe_headers, parse_rpc_response, probe_tools


def test_build_probe_headers_auth_matrix():
    assert build_probe_headers("bearer_token", "tok", {})["Authorization"] == "Bearer tok"
    assert build_probe_headers("basic", "dXNlcg==", {})["Authorization"] == "Basic dXNlcg=="
    assert build_probe_headers("api_key", "k1", {})["X-API-Key"] == "k1"
    h = build_probe_headers(None, None, {"X-Extra": "1"})
    assert h["X-Extra"] == "1" and "Authorization" not in h
    assert h["Accept"] == "application/json, text/event-stream"
    # blank value with auth_type set → no auth header (route resolves stored secret first)
    assert "Authorization" not in build_probe_headers("bearer_token", "", {})


def test_parse_rpc_response_sse_and_json():
    sse = httpx.Response(200, headers={"content-type": "text/event-stream"},
                         text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n')
    assert parse_rpc_response(sse)["result"] == {"ok": True}
    js = httpx.Response(200, headers={"content-type": "application/json"},
                        text='{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')
    assert parse_rpc_response(js)["result"] == {"ok": True}
    empty = httpx.Response(202)
    assert parse_rpc_response(empty) is None
    bad = httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: {nope\n")
    with pytest.raises(ProbeError):
        parse_rpc_response(bad)


def _rpc_server(tools, require_session=False, init_status=200, sse=True):
    """MockTransport for the 3-step probe. Returns (transport, seen)."""
    seen = {"headers": [], "methods": []}
    def respond(payload):
        if sse:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text=f"data: {json.dumps(payload)}\n\n")
        return httpx.Response(200, json=payload)
    def handler(req):
        body = json.loads(req.content) if req.content else {}
        seen["headers"].append(dict(req.headers))
        seen["methods"].append(body.get("method"))
        if body.get("method") == "initialize":
            r = respond({"jsonrpc": "2.0", "id": 1,
                         "result": {"serverInfo": {"name": "t", "version": "1"}}})
            if require_session:
                r.headers["mcp-session-id"] = "sess-1"
            if init_status != 200:
                return httpx.Response(init_status, text="denied")
            return r
        if body.get("method") == "notifications/initialized":
            if require_session and req.headers.get("mcp-session-id") != "sess-1":
                return httpx.Response(400, text="missing session")
            return httpx.Response(202)
        if body.get("method") == "tools/list":
            if require_session and req.headers.get("mcp-session-id") != "sess-1":
                return httpx.Response(400, text="missing session")
            return respond({"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}})
        return httpx.Response(404)
    return httpx.MockTransport(handler), seen


@pytest.mark.asyncio
async def test_probe_happy_path_sse():
    t, seen = _rpc_server([{"name": "a", "description": "da"}, {"name": "b"}])
    out = await probe_tools("http://x/mcp", http_transport=t)
    assert out == [{"name": "a", "description": "da"}, {"name": "b", "description": ""}]
    assert seen["methods"] == ["initialize", "notifications/initialized", "tools/list"]


@pytest.mark.asyncio
async def test_probe_happy_path_plain_json_and_session_echo():
    t, seen = _rpc_server([{"name": "a"}], require_session=True, sse=False)
    out = await probe_tools("http://x/mcp", http_transport=t)
    assert out[0]["name"] == "a"
    assert seen["headers"][2].get("mcp-session-id") == "sess-1"


@pytest.mark.asyncio
async def test_probe_auth_header_sent():
    t, seen = _rpc_server([{"name": "a"}])
    await probe_tools("http://x/mcp", auth_type="bearer_token", auth_value="tok", http_transport=t)
    assert seen["headers"][0].get("authorization") == "Bearer tok"


@pytest.mark.asyncio
async def test_probe_401_maps_to_probe_error():
    t, _ = _rpc_server([], init_status=401)
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/mcp", http_transport=t)
    assert "401" in str(ei.value)


@pytest.mark.asyncio
async def test_probe_rpc_error_object():
    def handler(req):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "error": {"code": -32000, "message": "boom"}})
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/mcp", http_transport=httpx.MockTransport(handler))
    assert "boom" in str(ei.value)


@pytest.mark.asyncio
async def test_probe_rejects_non_http_transport():
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/mcp", transport="sse")
    assert "HTTP transport" in str(ei.value)


@pytest.mark.asyncio
async def test_probe_connect_error_hides_url():
    def handler(req):
        raise httpx.ConnectError("secret-url-inside")
    with pytest.raises(ProbeError) as ei:
        await probe_tools("http://x/sk-EMBEDDED/mcp", http_transport=httpx.MockTransport(handler))
    assert "sk-EMBEDDED" not in str(ei.value) and "ConnectError" in str(ei.value)
```

- [ ] **Step 2: Run** — `pytest tests/test_mcp_probe.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** — `ui/app/mcp_probe.py`:

```python
from __future__ import annotations
import json
from typing import Any, Optional
import httpx

# Streamable-HTTP MCP probe: lets the UI list a server's tools BEFORE it is
# registered with LiteLLM (LiteLLM can only list tools for applied servers).
# Wire behavior live-verified against deepwiki 2.14.3 (2026-08-13): responses may
# be SSE-framed or plain JSON; mcp-session-id is optional.

_RPC_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}
_PROTOCOL_VERSION = "2025-03-26"


class ProbeError(RuntimeError):
    """Human-readable probe failure — message is safe to show in the UI."""


def build_probe_headers(auth_type, auth_value, static_headers) -> dict:
    """Exact LiteLLM auth mapping (experimental_mcp_client/client.py:351-376):
    bearer_token → Authorization: Bearer; basic → Authorization: Basic <verbatim,
    NOT base64'd here>; api_key → X-API-Key."""
    headers = dict(_RPC_HEADERS)
    for k, v in (static_headers or {}).items():
        headers[str(k)] = str(v)
    if auth_type and auth_value:
        if auth_type == "bearer_token":
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "basic":
            headers["Authorization"] = f"Basic {auth_value}"
        elif auth_type == "api_key":
            headers["X-API-Key"] = auth_value
    return headers


def parse_rpc_response(r: httpx.Response) -> Optional[dict]:
    """JSON-RPC payload from a streamable-HTTP response: SSE-framed (first data:
    line) or plain JSON. None for empty bodies (notification ACKs)."""
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in r.text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except Exception as e:
                    raise ProbeError(f"malformed SSE frame from server: {e.__class__.__name__}") from e
        return None
    if not r.content:
        return None
    try:
        return r.json()
    except Exception as e:
        raise ProbeError(f"malformed JSON from server: {e.__class__.__name__}") from e


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code in (401, 403):
        raise ProbeError(f"server returned {r.status_code} — check the auth type/value")
    if r.status_code >= 400:
        raise ProbeError(f"server returned HTTP {r.status_code}")


def _raise_for_rpc_error(msg) -> None:
    if isinstance(msg, dict) and msg.get("error"):
        e = msg["error"]
        detail = e.get("message") if isinstance(e, dict) else str(e)
        raise ProbeError(f"server error: {detail}")


async def probe_tools(url, auth_type=None, auth_value=None, static_headers=None,
                      transport="http", timeout=10.0,
                      http_transport: Optional[httpx.BaseTransport] = None) -> list[dict[str, Any]]:
    if transport != "http":
        raise ProbeError("direct preview supports the HTTP transport only — Apply the "
                         "server first and use the Tools browser, or type the tool names")
    headers = build_probe_headers(auth_type, auth_value, static_headers)
    async with httpx.AsyncClient(timeout=timeout, transport=http_transport) as c:
        try:
            r = await c.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                           "clientInfo": {"name": "llm-proxy-ui", "version": "1.0"}}})
            _raise_for_status(r)
            _raise_for_rpc_error(parse_rpc_response(r))
            sid = r.headers.get("mcp-session-id")
            if sid:
                headers["mcp-session-id"] = sid
            r2 = await c.post(url, headers=headers,
                              json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            _raise_for_status(r2)
            r3 = await c.post(url, headers=headers,
                              json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            _raise_for_status(r3)
            msg = parse_rpc_response(r3)
            _raise_for_rpc_error(msg)
        except ProbeError:
            raise
        except httpx.TimeoutException as e:
            raise ProbeError("server did not respond in time") from e
        # class name only: httpx error strings embed the URL, which can carry keys
        except httpx.HTTPError as e:
            raise ProbeError(f"could not reach the server: {e.__class__.__name__}") from e
    tools = (((msg or {}).get("result") or {}).get("tools")) or []
    return [{"name": t.get("name", ""), "description": t.get("description") or ""}
            for t in tools if isinstance(t, dict) and t.get("name")]
```

- [ ] **Step 4: Run** — targeted PASS; full suite green (362+9, 1 skipped).
- [ ] **Step 5: Commit** — `feat(ui): direct MCP tool probe (streamable HTTP, no SDK)` + trailer.

---

### Task 2: `POST /api/mcp/tools/preview`

**Files:** Modify `ui/app/routes/mcp_routes.py`; extend `ui/tests/test_mcp_routes.py`.

**Interfaces:**
- Consumes: `probe_tools`/`ProbeError` (Task 1); `effective` from `app.config_render`; `ConfigStore` from `app.config_db`; `fernet_from_secret` from `app.credentials_store` (same passphrase rule as config_v3_routes: `credentials_key or session_secret`).
- Produces: endpoint body `{url, transport, auth_type, auth_value, static_headers, server_id}` → `{"tools":[{"name","description"}]}`; ProbeError → 422 with its message; unexpected → 502 class-name only.

- [ ] **Step 1: Failing tests** — append to `ui/tests/test_mcp_routes.py` (reuse its `_client`/`FakeMcp` fixtures; add a monkeypatch seam for the probe):

```python
def test_preview_requires_login(tmp_path):
    c = _client(tmp_path, FakeMcp()); c.cookies.clear()
    assert c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp"}).status_code == 401


def test_preview_validates_url_and_transport(tmp_path):
    c = _client(tmp_path, FakeMcp())
    assert c.post("/api/mcp/tools/preview", json={"url": "ftp://x"}).status_code == 422
    r = c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp", "transport": "sse"})
    assert r.status_code == 422 and "HTTP transport" in r.json()["detail"]


def test_preview_calls_probe_and_returns_tools(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    seen = {}
    async def fake_probe(url, **kw):
        seen["url"] = url; seen["kw"] = kw
        return [{"name": "a", "description": "d"}]
    import app.routes.mcp_routes as mr
    monkeypatch.setattr(mr, "probe_tools", fake_probe)
    r = c.post("/api/mcp/tools/preview", json={
        "url": "http://x/mcp", "auth_type": "bearer_token", "auth_value": "tok",
        "static_headers": {"X-E": "1"}})
    assert r.status_code == 200 and r.json() == {"tools": [{"name": "a", "description": "d"}]}
    assert seen["kw"]["auth_value"] == "tok" and seen["kw"]["static_headers"] == {"X-E": "1"}


def test_preview_probe_error_maps_422(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    import app.routes.mcp_routes as mr
    from app.mcp_probe import ProbeError
    async def boom(url, **kw): raise ProbeError("server returned 401 — check the auth type/value")
    monkeypatch.setattr(mr, "probe_tools", boom)
    r = c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp"})
    assert r.status_code == 422 and "401" in r.json()["detail"]


def test_preview_blank_auth_uses_stored_secret(tmp_path, monkeypatch):
    c = _client(tmp_path, FakeMcp())
    # _client pins DATABASE_URL="" — the stored-secret path needs a non-empty DSN,
    # so override AFTER client creation and clear the lru_cached settings
    os.environ["DATABASE_URL"] = "fake://test"
    from app.settings import get_settings
    get_settings.cache_clear()
    import app.routes.mcp_routes as mr

    class FakeStore:
        async def applied(self):
            return [{"kind": "mcp_server", "name": "u1",
                     "data": {"server_name": "s", "auth_type": "bearer_token",
                              "auth_value_encrypted": "ENC:tok-stored"}}]
        async def staged(self): return []
    monkeypatch.setattr(mr, "make_preview_store", lambda: FakeStore())

    class FakeFernet:
        def decrypt(self, b): return b[4:]
    monkeypatch.setattr(mr, "_preview_fernet", lambda: FakeFernet())

    seen = {}
    async def fake_probe(url, **kw):
        seen["auth_value"] = kw.get("auth_value")
        return []
    monkeypatch.setattr(mr, "probe_tools", fake_probe)
    r = c.post("/api/mcp/tools/preview", json={
        "url": "http://x/mcp", "auth_type": "bearer_token", "auth_value": "", "server_id": "u1"})
    assert r.status_code == 200 and seen["auth_value"] == "tok-stored"
    os.environ.pop("DATABASE_URL", None)


def test_preview_blank_auth_without_stored_secret_422(tmp_path):
    c = _client(tmp_path, FakeMcp())
    r = c.post("/api/mcp/tools/preview", json={"url": "http://x/mcp", "auth_type": "api_key"})
    assert r.status_code == 422 and "auth_value" in r.json()["detail"]
```

- [ ] **Step 2: Run** — FAIL (endpoint missing).

- [ ] **Step 3: Implement** — in `ui/app/routes/mcp_routes.py` add imports and the endpoint:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Body   # extend existing import
from app.mcp_probe import probe_tools, ProbeError
from app.config_render import effective
from app.config_db import ConfigStore
from app.credentials_store import fernet_from_secret


def make_preview_store() -> ConfigStore:
    return ConfigStore(get_settings().database_url)


def _preview_fernet():
    s = get_settings()
    return fernet_from_secret(s.credentials_key or s.session_secret)


@router.post("/mcp/tools/preview", dependencies=[Depends(login_required)])
async def mcp_tools_preview(body: dict = Body(...)):
    """List an MCP server's tools by probing it DIRECTLY (works before the server
    is saved/applied — LiteLLM can only list tools for registered servers).
    Blank auth_value + server_id reuses the stored ciphertext (blank-means-keep parity)."""
    url = (body.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="url required (http:// or https://)")
    transport = body.get("transport") or "http"
    auth_type = body.get("auth_type") or None
    auth_value = body.get("auth_value") or ""
    if auth_type and not auth_value:
        server_id = body.get("server_id")
        dsn = get_settings().database_url
        if not (server_id and dsn):
            raise HTTPException(status_code=422, detail="auth_value required")
        store = make_preview_store()
        eff = effective(await store.applied(), await store.staged())
        existing = next((i for i in eff if i["kind"] == "mcp_server" and i["name"] == server_id
                         and i.get("flag") != "deleted"), None)
        ve = (existing.get("data") or {}).get("auth_value_encrypted") if existing else None
        if not ve:
            raise HTTPException(status_code=422, detail="auth_value required (no stored secret to reuse)")
        auth_value = _preview_fernet().decrypt(ve.encode()).decode()
    try:
        tools = await probe_tools(url, auth_type=auth_type, auth_value=auth_value,
                                  static_headers=body.get("static_headers") or {},
                                  transport=transport)
    except ProbeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        # never echo exception text: it can carry the URL (which can embed keys)
        raise HTTPException(status_code=502, detail=f"probe failed: {e.__class__.__name__}")
    return {"tools": tools}
```

Note the FakeFernet in the stored-secret test returns bytes from `b[4:]` — the route calls `.decode()` on the result, and `b"ENC:tok-stored".encode()` wire: the route does `ve.encode()` (str→bytes) then `.decrypt(...)` then `.decode()`. Make FakeFernet consistent: `def decrypt(self, b): return b[4:]` receives `b"ENC:tok-stored"` and returns `b"tok-stored"`; `.decode()` → `"tok-stored"`. Correct as written.

- [ ] **Step 4: Run** — targeted PASS; full suite green.
- [ ] **Step 5: Commit** — `feat(ui): /api/mcp/tools/preview (pre-save tool discovery)` + trailer.

---

### Task 3: `mergeToolChoices` + api entry

**Files:** Modify `ui/frontend/src/lib/mcp.js`, `ui/frontend/src/lib/api.js`.

**Interfaces:** Produces (Task 4): `mergeToolChoices(fetched, existing) -> {choices, extras}`; `api.mcpToolsPreview(body)`.

- [ ] **Step 1: Implement** — append to `ui/frontend/src/lib/mcp.js`:

```js
export function mergeToolChoices(fetched, existing) {
  // Convergent merge: fetched tools become checkboxes (checked = already allowed);
  // existing entries NOT in the fetched list survive as editable rows (extras) so
  // a typed/renamed/offline tool is never silently dropped by a re-fetch.
  const names = new Set((existing || []).map(s => (typeof s === 'string' ? s : '').trim()).filter(Boolean))
  const choices = []
  const seen = new Set()
  for (const t of fetched || []) {
    const name = (t?.name || '').trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    choices.push({ name, description: t?.description || '', checked: names.has(name) })
  }
  const extras = [...names].filter(n => !seen.has(n))
  return { choices, extras }
}
```

and to `ui/frontend/src/lib/api.js` (with the other mcp entries):

```js
  mcpToolsPreview: (body) => req('/api/mcp/tools/preview', { method: 'POST', body: JSON.stringify(body) }),
```

- [ ] **Step 2: Node asserts** (from `ui/frontend`):

```bash
node --input-type=module -e "
import { mergeToolChoices } from './src/lib/mcp.js'
import assert from 'node:assert'
const r = mergeToolChoices([{name:'a',description:'d'},{name:'b'},{name:'a'}], ['b',' typed ',''])
assert.deepEqual(r.choices, [{name:'a',description:'d',checked:false},{name:'b',description:'',checked:true}])
assert.deepEqual(r.extras, ['typed'])
// convergence: feeding the merged state back in loses nothing
const names2 = [...r.choices.filter(c=>c.checked).map(c=>c.name), ...r.extras]
const r2 = mergeToolChoices([{name:'a'},{name:'b'}], names2)
assert.deepEqual(r2.extras, ['typed'])
assert.equal(r2.choices.find(c=>c.name==='b').checked, true)
assert.deepEqual(mergeToolChoices([], []), {choices:[], extras:[]})
console.log('mergeToolChoices OK')
"
```
Expected: `mergeToolChoices OK`. Build clean.

- [ ] **Step 3: Commit** — `feat(ui): mergeToolChoices helper + preview api entry` + trailer.

---

### Task 4: Picker UI in McpServers.svelte

**Files:** Modify `ui/frontend/src/routes/McpServers.svelte`.

**Interfaces:** Consumes Task 3's helper + endpoint. All anchors below exist verbatim (the file matches the v3.27 plan's component).

- [ ] **Step 1: Script changes.**

Extend the lib/mcp.js import line with `mergeToolChoices`. After the `costRows` state line add:

```js
  let toolChoices = $state([])     // [{name, description, checked}] after a Fetch tools
  let toolFetchBusy = $state(false)
  let toolFetchErr = $state('')
```

In `resetForm()` and `editServer()` add: `toolChoices = []; toolFetchErr = ''` (both — a re-edit starts from rows, not stale choices).

Add functions (near the other row helpers):

```js
  async function fetchTools() {
    toolFetchBusy = true; toolFetchErr = ''
    try {
      const r = await api.mcpToolsPreview({
        url: form.url.trim(),
        transport: form.transport,
        auth_type: form.auth_type || null,
        auth_value: form.auth_value,           // blank on edit = stored secret (server-side)
        static_headers: headerRowsToDict(headerRows),
        server_id: editingId,
      })
      const merged = mergeToolChoices(r.tools ?? [], [
        ...toolChoices.filter(c => c.checked).map(c => c.name),
        ...listRowsToArray(toolRows),
      ])
      toolChoices = merged.choices
      toolRows = merged.extras
    } catch (e) { toolFetchErr = e.message }
    finally { toolFetchBusy = false }
  }
  function toggleToolChoice(name) {
    toolChoices = toolChoices.map(c => c.name === name ? { ...c, checked: !c.checked } : c)
  }
```

In `saveServer()` change the `allowed_tools` line to:

```js
      allowed_tools: [...new Set([
        ...toolChoices.filter(c => c.checked).map(c => c.name),
        ...listRowsToArray(toolRows),
      ])],
```

- [ ] **Step 2: Markup** — replace the Allowed tools `.rows` block with:

```svelte
      <div class="rows">
        <span class="field-name">Allowed tools <span class="hint">(blank = all tools exposed)</span>
          <button type="button" class="addrow" onclick={fetchTools}
                  disabled={toolFetchBusy || !form.url.trim()}>
            {toolFetchBusy ? 'Fetching…' : '⟳ Fetch tools'}
          </button>
        </span>
        {#if toolFetchErr}<span class="fetch-err">{toolFetchErr}</span>{/if}
        {#each toolChoices as c (c.name)}
          <label class="tool-choice">
            <input type="checkbox" checked={c.checked} onchange={() => toggleToolChoice(c.name)} />
            <code>{c.name}</code>{#if c.description}<span class="hint"> — {c.description}</span>{/if}
          </label>
        {/each}
        {#each toolRows as _, i}
          <div class="kv-row">
            <input placeholder="tool name" bind:value={toolRows[i]} />
            <button type="button" class="x" onclick={() => rmTool(i)} aria-label="remove">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addTool}>+ Add tool</button>
      </div>
```

CSS additions:

```css
  .tool-choice{display:flex;align-items:baseline;gap:8px;font-size:13px;margin:2px 0}
  .fetch-err{font-size:11px;color:#b00020}
```

- [ ] **Step 3: Build** — `npm run build` clean; backend suite unchanged.
- [ ] **Step 4: Commit** — `feat(ui): fetched Allowed-tools picker on the MCP server form` + trailer.

---

### Task 5: e2e + docs

**Files:** Modify `docs/mcp-gateway.md`.

- [ ] **Step 1: Live e2e on the local stack** (branch backend on the LAN IP, as in v3.27 Task 13; Playwright browser tools available via ToolSearch):
  1. Add form: enter url `https://mcp.deepwiki.com/mcp`, transport HTTP, no auth → **Fetch tools** → the 3 deepwiki tools appear as unchecked checkboxes with descriptions. Tick one → Save → staged item's `allowed_tools` contains exactly it (verify via `/api/config/state`).
  2. Pre-fill a typed row `made_up_tool`, then Fetch → it survives as an extra row; checkbox list unaffected.
  3. Transport SSE + Fetch → inline friendly message (contains "HTTP transport").
  4. Stored-secret path: stage+apply a bearer-auth server (dummy secret ok — deepwiki ignores auth headers: enter url deepwiki, auth bearer `dummy`, apply), re-edit (auth field blank/`(unchanged)`), Fetch → tools load (probe used the decrypted stored value; deepwiki accepts anyway — the assertion is a 200 through the stored-secret code path, verified by the earlier unit test for correctness).
  5. Cleanup: discard/delete anything staged; leave master config as found.
- [ ] **Step 2: Docs** — in `docs/mcp-gateway.md`, in the section describing the server form's Allowed tools, add a short paragraph: Fetch tools probes the URL/auth currently in the form (works before Apply; HTTP transport only — SSE servers: Apply first and use the Tools browser); previously-typed names not present on the server remain as manual rows.
- [ ] **Step 3: Final green run** — full pytest suite + `npm run build`.
- [ ] **Step 4: Commit** — `docs: fetch-tools picker in MCP gateway guide` + trailer.

---

## After the plan

Whole-branch final review → merge --no-ff → CI cuts **1.34.0** → pin bump → `.75` UI-only deploy → memory update. (v3.29 per-key tool ACLs starts a fresh brainstorm→proof→spec cycle.)
