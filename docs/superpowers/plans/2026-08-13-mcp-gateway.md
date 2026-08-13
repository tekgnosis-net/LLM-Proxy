# MCP Gateway (v3.27) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manage MCP servers behind LLM-Proxy as a hot-applied master/servant config entity, with per-key ACLs, health/tools/usage reporting, and Activity-feed visibility.

**Architecture:** New `mcp_server` config kind in ui_config (staged Save→Apply), converged to LiteLLM's live registry via a reconciler over `/v1/mcp/server` CRUD (hot — never rendered to config.yaml, never restarts litellm). Per-key grants ride LiteLLM's `object_permission.mcp_servers`. Reporting reads `LiteLLM_SpendLogs` (`call_type='call_mcp_tool'`) and the health columns of `LiteLLM_MCPServerTable`.

**Tech Stack:** FastAPI + asyncpg + httpx (backend, `ui/app`), Svelte 5 runes + Vite (frontend, `ui/frontend`), pytest + httpx.MockTransport, node module tests.

**Spec:** `docs/superpowers/specs/2026-08-13-mcp-gateway-design.md` (approved). **Branch:** `v3.27-mcp-gateway` (exists; spec committed).

## Global Constraints

- Config kind literal: `mcp_server`. Item name = UUID = LiteLLM `server_id`.
- Stored data fields (exact): `server_name, description, transport, url, auth_type, auth_value_encrypted, static_headers, extra_headers, allowed_tools, allow_all_keys, mcp_info`.
- `transport` ∈ {`http`, `sse`}. `auth_type` ∈ {`api_key`, `bearer_token`, `basic`} or null. `server_name` must match `^[A-Za-z0-9_-]+$` and be unique among non-deleted `mcp_server` items.
- LiteLLM endpoints (verified in image): `GET/POST/PUT /v1/mcp/server`, `GET/DELETE /v1/mcp/server/{server_id}`, `GET /v1/mcp/server/health?server_ids=`, `GET /mcp-rest/tools/list?server_id=`, `POST /mcp-rest/tools/call`. Wire secret: `credentials: {"auth_value": "<plaintext>"}` sent whenever `auth_type` is set.
- `mcp_server` must NOT be added to `_RESTART_KINDS` (`ui/app/config_engine.py:11`) and must never be rendered into config.yaml.
- Secret at rest in master: Fernet ciphertext under `auth_value_encrypted`; redaction placeholder on read: `***`; blank `auth_value` on edit = keep stored ciphertext.
- Per-key grants: `object_permission: {"mcp_servers": ["<item-uuid>", ...]}` on `/key/generate|update`; UUIDs, not names.
- SpendLogs MCP call types: `call_mcp_tool`, `list_mcp_tools`; metadata path `metadata.mcp_tool_call_metadata.{mcp_server_name,name,arguments,result}`.
- Backend tests: `cd /home/kumar/workspace/litellm/ui && .venv/bin/python -m pytest tests/ -q` (suite currently 309 passed, 1 skipped — keep green). Frontend build: `cd /home/kumar/workspace/litellm/ui/frontend && npm run build`. Node lib tests: `node --input-type=module -e '...'` run from `ui/frontend`.
- Commits: conventional (`feat:`/`fix:`/`test:`/`docs:`), each ends with the trailer line `Claude-Session: https://claude.ai/code/session_011o3rL25n2rCTBGrXP5uPYE`. NO AI-attribution lines.
- Never commit `.env`. Never rotate `SESSION_SECRET`/`CREDENTIALS_KEY`/`LITELLM_SALT_KEY`. Master key stays server-side. Dev servers bind `0.0.0.0` / LAN IP, never localhost-only.
- LiteLLM image floats (`main-stable`): Task 1 re-verifies every wire fact before code is written; on hard mismatch STOP and escalate.

## File Structure

| File | Change |
|---|---|
| `ui/app/mcp_client.py` | NEW — httpx client for LiteLLM MCP admin endpoints |
| `ui/app/mcp_reconcile.py` | NEW — build_desired / diff_mcp / reconcile_mcp / mcp_content_diff |
| `ui/app/config_engine.py` | mcp wiring in apply_config + non-hybrid guard |
| `ui/app/config_render.py` | explicit no-op branch comment for `mcp_server` |
| `ui/app/config_import.py` | `mcp_servers:` YAML → items on bootstrap import |
| `ui/app/config_integrity.py` | `mcp_server_names`, `key_mcp_orphans`, trim support |
| `ui/app/routes/config_v3_routes.py` | `_mcp_server_data` staging validation/encryption, `_redact_item`, apply/drift/resync/integrity wiring, `make_mcp_client` |
| `ui/app/routes/keys_routes.py` | `_validate_key_refs` gains MCP-grant validation |
| `ui/app/routes/mcp_routes.py` | NEW — `/api/mcp/health`, `/api/mcp/tools`, `/api/mcp/usage` |
| `ui/app/routes/usage_routes.py` | activity SELECT + type filter + tx MCP extraction |
| `ui/app/main.py` | register `mcp_routes` |
| `ui/frontend/src/lib/mcp.js` | NEW — pure form/wire converters |
| `ui/frontend/src/lib/api.js` | `mcpHealth/mcpTools/mcpUsage` |
| `ui/frontend/src/lib/configStore.svelte.js` | apply-result message includes MCP report |
| `ui/frontend/src/routes/McpServers.svelte` | NEW page |
| `ui/frontend/src/App.svelte` | nav button + route branch |
| `ui/frontend/src/routes/Keys.svelte` | MCP grants picker |
| `ui/frontend/src/routes/ActivityFeed.svelte` | MCP rows, type filter, detail pane |
| `ui/tests/test_mcp_client.py`, `ui/tests/test_mcp_reconcile.py`, `ui/tests/test_mcp_routes.py` | NEW test files |
| `ui/tests/test_config_v3_routes.py`, `ui/tests/test_config_engine.py`, `ui/tests/test_config_import.py`, `ui/tests/test_config_integrity.py`, `ui/tests/test_keys_routes.py`, `ui/tests/test_usage_activity.py` | extended |
| `docs/mcp-gateway.md` | NEW — client onboarding + admin guide |

---

### Task 1: Live platform proof (no production code)

**Files:** none (writes `.superpowers/sdd/task-1-report.md` with recorded facts). Read-only vs the repo.

**Interfaces:**
- Produces: recorded wire facts consumed by Tasks 2–8: (a) `GET /v1/mcp/server` response shape (bare list vs `{data: []}`), (b) DELETE response body, (c) health endpoint response shape, (d) whether `object_permission` appears in `/key/list?return_full_object=true`, (e) tool-name prefix separator, (f) confirmation that per-key MCP ACL enforces on OSS and clear-to-empty revokes.

This is the passthrough-lesson gate: **no UI code before live proof.** Run against the LOCAL stack (image `ghcr.io/berriai/litellm:main-stable`, same as `.75`).

- [ ] **Step 1: Bring up the local stack and resolve ports**

```bash
cd /home/kumar/workspace/litellm
docker compose up -d && sleep 20
BASE="http://$(docker compose port litellm 4000)"
MASTER_KEY=$(grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)
curl -sS "$BASE/health/readiness" -H "Authorization: Bearer $MASTER_KEY"
```
Expected: readiness JSON with `"status":"healthy"` (or equivalent).

- [ ] **Step 2: Create an MCP server via the admin API (deepwiki, no auth)**

```bash
curl -sS -X POST "$BASE/v1/mcp/server" -H "Authorization: Bearer $MASTER_KEY" \
  -H 'content-type: application/json' \
  -d '{"server_id":"e2e-proof-1","server_name":"deepwiki","transport":"http","url":"https://mcp.deepwiki.com/mcp","description":"proof"}'
```
Expected: 200 with a JSON object echoing `server_id: "e2e-proof-1"`. Record the exact response.

- [ ] **Step 3: List servers as admin — record shape + redaction**

```bash
curl -sS "$BASE/v1/mcp/server" -H "Authorization: Bearer $MASTER_KEY"
```
Record: bare list or `{data: [...]}`; confirm `credentials` is null/absent; record which of our managed fields appear and their spelling (`server_name`, `transport`, `url`, `allow_all_keys`, `mcp_info`, `status`, `last_health_check`).

- [ ] **Step 4: Tools list via the gateway REST surface (master key)**

```bash
curl -sS "$BASE/mcp-rest/tools/list?server_id=e2e-proof-1" -H "Authorization: Bearer $MASTER_KEY"
```
Expected: tool list including deepwiki tools (e.g. `read_wiki_structure`). Record the prefixed tool-name format (e.g. `deepwiki-read_wiki_structure`) and the response envelope.

- [ ] **Step 5: Per-key ACL proof — deny, allow, revoke**

```bash
# key with NO grant
K1=$(curl -sS -X POST "$BASE/key/generate" -H "Authorization: Bearer $MASTER_KEY" \
  -H 'content-type: application/json' -d '{"key_alias":"mcp-proof-nogrant"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"])')
# key WITH grant
K2=$(curl -sS -X POST "$BASE/key/generate" -H "Authorization: Bearer $MASTER_KEY" \
  -H 'content-type: application/json' \
  -d '{"key_alias":"mcp-proof-grant","object_permission":{"mcp_servers":["e2e-proof-1"]}}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"])')
echo "--- no grant (expect empty/denied):"; curl -sS "$BASE/v1/mcp/server" -H "x-litellm-api-key: $K1"
echo "--- grant (expect deepwiki visible):"; curl -sS "$BASE/v1/mcp/server" -H "x-litellm-api-key: $K2"
echo "--- no grant tools (expect empty/403):"; curl -sS "$BASE/mcp-rest/tools/list" -H "x-litellm-api-key: $K1"
echo "--- grant tools (expect deepwiki tools):"; curl -sS "$BASE/mcp-rest/tools/list" -H "x-litellm-api-key: $K2"
# revoke: clear to empty list
curl -sS -X POST "$BASE/key/update" -H "Authorization: Bearer $MASTER_KEY" -H 'content-type: application/json' \
  -d "{\"key\": \"$K2\", \"object_permission\": {\"mcp_servers\": []}}"
echo "--- after revoke (expect empty again):"; curl -sS "$BASE/mcp-rest/tools/list" -H "x-litellm-api-key: $K2"
```
**ABORT CRITERIA:** if `/key/generate` rejects `object_permission` with a premium/Enterprise error, or the no-grant key sees the server anyway, or revoke does not take effect — STOP, report BLOCKED, escalate (fallback design: `allow_all_keys` + client-side `x-mcp-servers` scoping — a spec change the human must approve).

- [ ] **Step 6: `object_permission` visible in key listing**

```bash
# re-grant first so there is something to see
curl -sS -X POST "$BASE/key/update" -H "Authorization: Bearer $MASTER_KEY" -H 'content-type: application/json' \
  -d "{\"key\": \"$K2\", \"object_permission\": {\"mcp_servers\": [\"e2e-proof-1\"]}}" >/dev/null
curl -sS "$BASE/key/list?return_full_object=true&page=1&size=50" -H "Authorization: Bearer $MASTER_KEY" \
  | python3 -c 'import sys,json;[print(k.get("key_alias"), json.dumps(k.get("object_permission"))) for k in json.load(sys.stdin)["keys"]]'
```
Record whether `object_permission.mcp_servers` is present for `mcp-proof-grant`. If absent, record it — Task 11 then populates the picker via `GET /key/info?key=<token>` instead (note this in the report; Task 11 has the fallback branch).

- [ ] **Step 7: Tool call + spend log row**

```bash
curl -sS -X POST "$BASE/mcp-rest/tools/call" -H "x-litellm-api-key: $K2" -H 'content-type: application/json' \
  -d '{"server_id":"e2e-proof-1","name":"read_wiki_structure","arguments":{"repoName":"BerriAI/litellm"}}'
sleep 5
docker compose exec -T db psql -U "$(grep '^POSTGRES_USER=' .env | cut -d= -f2-)" \
  -d "$(grep '^POSTGRES_DB=' .env | cut -d= -f2-)" -c \
  "SELECT call_type, metadata::jsonb #>> '{mcp_tool_call_metadata,mcp_server_name}' AS server, metadata::jsonb #>> '{mcp_tool_call_metadata,name}' AS tool FROM \"LiteLLM_SpendLogs\" ORDER BY \"startTime\" DESC LIMIT 3;"
```
Expected: a row with `call_type=call_mcp_tool`, server `deepwiki`, tool name populated. This proves the exact jsonb paths Tasks 8/12 use. Record actual values. (If the psql service name differs, find it with `docker compose ps`.)

- [ ] **Step 8: Health endpoint shape**

```bash
curl -sS "$BASE/v1/mcp/server/health?server_ids=e2e-proof-1" -H "Authorization: Bearer $MASTER_KEY"
```
Record the response shape verbatim (Task 8's `/api/mcp/health` passes it through as `probe`).

- [ ] **Step 9: Cleanup + report**

```bash
curl -sS -X DELETE "$BASE/v1/mcp/server/e2e-proof-1" -H "Authorization: Bearer $MASTER_KEY"
# delete both proof keys (tokens from the generate responses' "token" field, or via key/list)
```
Write `.superpowers/sdd/task-1-report.md` with every recorded fact + verbatim JSON samples. No commit (report dir is git-ignored). Report DONE with the facts summarized.

---

### Task 2: `mcp_client.py`

**Files:**
- Create: `ui/app/mcp_client.py`
- Test: `ui/tests/test_mcp_client.py`

**Interfaces:**
- Consumes: Task 1's recorded list/DELETE shapes (adjust the unwrap in `list_servers` if Task 1 recorded `{data: [...]}` — the code below handles both).
- Produces: `McpClient(base_url, master_key, transport=None)` with `async list_servers() -> list[dict]`, `add_server(payload) -> dict`, `update_server(payload) -> dict`, `delete_server(server_id) -> dict`, `health(server_ids: list[str] | None) -> Any`, `list_tools(server_id) -> Any`. Used by Tasks 5, 6, 8.

- [ ] **Step 1: Write failing tests** — `ui/tests/test_mcp_client.py`:

```python
import json, httpx, pytest
from app.mcp_client import McpClient


def _client(handler):
    return McpClient("http://litellm:4000", "sk-master", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_servers_auth_and_unwrap():
    def handler(req):
        assert req.headers["authorization"] == "Bearer sk-master"
        assert req.url.path == "/v1/mcp/server"
        return httpx.Response(200, json=[{"server_id": "u1", "server_name": "deepwiki", "credentials": None}])
    out = await _client(handler).list_servers()
    assert out[0]["server_id"] == "u1"


@pytest.mark.asyncio
async def test_list_servers_unwraps_data_envelope():
    def handler(req):
        return httpx.Response(200, json={"data": [{"server_id": "u1"}]})
    out = await _client(handler).list_servers()
    assert out == [{"server_id": "u1"}]


@pytest.mark.asyncio
async def test_add_server_posts_payload():
    seen = {}
    def handler(req):
        seen["method"], seen["path"], seen["body"] = req.method, req.url.path, json.loads(req.content)
        return httpx.Response(200, json={"server_id": "u1"})
    await _client(handler).add_server({"server_id": "u1", "server_name": "s", "url": "http://x/mcp"})
    assert seen["method"] == "POST" and seen["path"] == "/v1/mcp/server"
    assert seen["body"]["server_id"] == "u1"


@pytest.mark.asyncio
async def test_update_server_puts_and_requires_id():
    seen = {}
    def handler(req):
        seen["method"], seen["path"] = req.method, req.url.path
        return httpx.Response(200, json={"server_id": "u1"})
    c = _client(handler)
    await c.update_server({"server_id": "u1", "url": "http://x/mcp"})
    assert seen["method"] == "PUT" and seen["path"] == "/v1/mcp/server"
    with pytest.raises(ValueError):
        await c.update_server({"url": "http://x/mcp"})


@pytest.mark.asyncio
async def test_delete_server_quotes_id_and_tolerates_empty_body():
    seen = {}
    def handler(req):
        seen["method"], seen["path"] = req.method, req.url.path
        return httpx.Response(200)   # empty body
    out = await _client(handler).delete_server("u 1")
    assert seen["method"] == "DELETE" and seen["path"] == "/v1/mcp/server/u%201"
    assert out == {}


@pytest.mark.asyncio
async def test_health_and_tools_params():
    seen = {}
    def handler(req):
        seen["path"], seen["query"] = req.url.path, str(req.url.query, "utf-8")
        return httpx.Response(200, json={"ok": True})
    c = _client(handler)
    await c.health(["a", "b"])
    assert seen["path"] == "/v1/mcp/server/health" and "server_ids=a" in seen["query"] and "server_ids=b" in seen["query"]
    await c.list_tools("u1")
    assert seen["path"] == "/mcp-rest/tools/list" and "server_id=u1" in seen["query"]


@pytest.mark.asyncio
async def test_error_raises():
    def handler(req):
        return httpx.Response(500, text="boom")
    with pytest.raises(httpx.HTTPError):
        await _client(handler).list_servers()
```

- [ ] **Step 2: Run to verify failure** — `cd /home/kumar/workspace/litellm/ui && .venv/bin/python -m pytest tests/test_mcp_client.py -q` → FAIL `ModuleNotFoundError: app.mcp_client`.

- [ ] **Step 3: Implement** — `ui/app/mcp_client.py`:

```python
from __future__ import annotations
import httpx
from typing import Any, Optional
from urllib.parse import quote


class McpClient:
    """Async client for LiteLLM MCP-gateway admin endpoints (requires
    STORE_MODEL_IN_DB=true on the proxy). Master key stays server-side.
    GET /v1/mcp/server returns servers with credentials REDACTED (null) —
    live state is presence/content for non-secret fields only."""

    def __init__(self, base_url: str, master_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=15.0, transport=self._transport)

    async def list_servers(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"{self._base}/v1/mcp/server")
            r.raise_for_status()
            data = r.json()
            return data.get("data", data) if isinstance(data, dict) else data

    async def add_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(f"{self._base}/v1/mcp/server", json=payload)
            r.raise_for_status()
            return r.json()

    async def update_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("server_id"):
            raise ValueError("update_server requires server_id")
        async with self._client() as c:
            r = await c.put(f"{self._base}/v1/mcp/server", json=payload)
            r.raise_for_status()
            return r.json()

    async def delete_server(self, server_id: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.delete(f"{self._base}/v1/mcp/server/{quote(str(server_id), safe='')}")
            r.raise_for_status()
            return r.json() if r.content else {}

    async def health(self, server_ids: list[str] | None = None) -> Any:
        params = [("server_ids", s) for s in (server_ids or [])]
        async with self._client() as c:
            r = await c.get(f"{self._base}/v1/mcp/server/health", params=params)
            r.raise_for_status()
            return r.json()

    async def list_tools(self, server_id: str) -> Any:
        async with self._client() as c:
            r = await c.get(f"{self._base}/mcp-rest/tools/list", params={"server_id": server_id})
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4: Run tests** — same command → all PASS. Full suite still green: `.venv/bin/python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add ui/app/mcp_client.py ui/tests/test_mcp_client.py
git commit -m "feat(ui): McpClient for LiteLLM MCP admin endpoints

Claude-Session: https://claude.ai/code/session_011o3rL25n2rCTBGrXP5uPYE"
```

---

### Task 3: `mcp_reconcile.py`

**Files:**
- Create: `ui/app/mcp_reconcile.py`
- Test: `ui/tests/test_mcp_reconcile.py`

**Interfaces:**
- Consumes: items shaped `{kind:"mcp_server", name:<uuid>, data:{...spec §4 fields...}}`; `McpClient` (Task 2); `_is_already_exists` from `app.model_reconcile`.
- Produces (used by Tasks 5, 6):
  - `build_desired(items, decrypt) -> (dict[str, dict], list[dict])` — `{server_id: wire_payload}`, failures. `decrypt=None` → presence/content-only payloads (no credentials).
  - `diff_mcp(desired, live, changed_ids) -> {"to_add": [...], "to_update": [...], "to_delete": [...]}`
  - `reconcile_mcp(desired_items, live, client, changed_item_names, decrypt) -> {"added", "updated", "deleted", "failed"}`
  - `MCP_MANAGED_FIELDS` tuple and `mcp_content_diff(desired_payload, live_server) -> list[str]`

- [ ] **Step 1: Write failing tests** — `ui/tests/test_mcp_reconcile.py`:

```python
import pytest
from app.mcp_reconcile import build_desired, diff_mcp, reconcile_mcp, mcp_content_diff

DEC = lambda b: b[4:] if b.startswith("ENC:") else b

def _item(name, **over):
    data = {"server_name": "s-" + name, "transport": "http", "url": f"http://h/{name}/mcp",
            "auth_type": None, "static_headers": {}, "extra_headers": [], "allowed_tools": [],
            "allow_all_keys": False, "mcp_info": {}}
    data.update(over)
    return {"kind": "mcp_server", "name": name, "data": data}


def test_build_desired_payload_and_credentials():
    desired, failed = build_desired(
        [_item("u1"), _item("u2", auth_type="bearer_token", auth_value_encrypted="ENC:tok")], DEC)
    assert failed == []
    assert desired["u1"]["server_id"] == "u1" and desired["u1"]["server_name"] == "s-u1"
    assert "credentials" not in desired["u1"]
    assert desired["u2"]["credentials"] == {"auth_value": "tok"}
    assert desired["u2"]["auth_type"] == "bearer_token"


def test_build_desired_presence_only_skips_decrypt():
    desired, failed = build_desired([_item("u2", auth_type="bearer_token", auth_value_encrypted="ENC:tok")], None)
    assert failed == [] and "credentials" not in desired["u2"]


def test_build_desired_decrypt_failure_reported():
    def boom(_): raise ValueError("bad token")
    desired, failed = build_desired([_item("u1", auth_type="api_key", auth_value_encrypted="x")], boom)
    assert desired == {} and failed[0]["op"] == "decrypt"


def test_diff_mcp_add_update_delete():
    desired, _ = build_desired([_item("a"), _item("b")], DEC)
    live = [{"server_id": "b"}, {"server_id": "c"}]
    plan = diff_mcp(desired, live, changed_ids={"b"})
    assert [e["server_id"] for e in plan["to_add"]] == ["a"]
    assert [e["server_id"] for e in plan["to_update"]] == ["b"]
    assert plan["to_delete"] == ["c"]


class FakeClient:
    def __init__(self, fail_add=None):
        self.added, self.updated, self.deleted = [], [], []
        self._fail_add = fail_add or set()
    async def add_server(self, p):
        if p["server_id"] in self._fail_add:
            raise RuntimeError("already exists")
        self.added.append(p["server_id"])
    async def update_server(self, p): self.updated.append(p["server_id"])
    async def delete_server(self, sid): self.deleted.append(sid)


@pytest.mark.asyncio
async def test_reconcile_mcp_converges():
    items = [_item("a"), _item("b")]
    live = [{"server_id": "b"}, {"server_id": "gone"}]
    c = FakeClient()
    rep = await reconcile_mcp(items, live, c, changed_item_names={"b"}, decrypt=DEC)
    assert c.added == ["a"] and c.updated == ["b"] and c.deleted == ["gone"]
    assert rep == {"added": 1, "updated": 1, "deleted": 1, "failed": []}


@pytest.mark.asyncio
async def test_reconcile_add_collision_becomes_update():
    c = FakeClient(fail_add={"a"})
    rep = await reconcile_mcp([_item("a")], [], c, changed_item_names=set(), decrypt=DEC)
    assert c.updated == ["a"] and rep["updated"] == 1 and rep["failed"] == []


def test_content_diff_normalizes_empty():
    desired, _ = build_desired([_item("a")], None)
    live = {"server_id": "a", "server_name": "s-a", "transport": "http", "url": "http://h/a/mcp",
            "auth_type": None, "static_headers": None, "extra_headers": None, "allowed_tools": None,
            "allow_all_keys": False, "mcp_info": None, "status": "healthy"}
    assert mcp_content_diff(desired["a"], live) == []
    live2 = dict(live, url="http://other/mcp")
    assert mcp_content_diff(desired["a"], live2) == ["url"]
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_mcp_reconcile.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** — `ui/app/mcp_reconcile.py`:

```python
from __future__ import annotations
from typing import Any, Callable, Optional

from app.model_reconcile import _is_already_exists

# LiteLLM redacts `credentials` in GET /v1/mcp/server, so live state is used for
# presence + non-secret content only. Credentials are (re)sent on every add/update
# where auth_type is set — idempotent; LiteLLM re-encrypts at rest with the salt key.

MCP_MANAGED_FIELDS = ("server_name", "description", "transport", "url", "auth_type",
                      "static_headers", "extra_headers", "allowed_tools",
                      "allow_all_keys", "mcp_info")


def _norm_deep(v):
    """None == '' == [] == {} for drift comparison; recurses into containers."""
    if isinstance(v, dict):
        d = {k: _norm_deep(x) for k, x in v.items()}
        d = {k: x for k, x in d.items() if x is not None}
        return d or None
    if isinstance(v, list):
        out = [_norm_deep(x) for x in v]
        out = [x for x in out if x is not None]
        return out or None
    if v is None or v == "":
        return None
    return v


def mcp_content_diff(desired: dict, live: dict) -> list[str]:
    """Managed-field comparison; credentials never compared (redacted in live)."""
    return [f for f in MCP_MANAGED_FIELDS
            if _norm_deep(desired.get(f)) != _norm_deep(live.get(f))]


def build_desired(items, decrypt: Optional[Callable[[str], str]]):
    """{server_id: wire payload} from mcp_server items. decrypt=None → no credentials
    (presence/drift use). A ciphertext that fails to decrypt becomes a 'failed' entry."""
    desired: dict[str, dict] = {}
    failed: list[dict] = []
    for it in items:
        d = it["data"] or {}
        payload: dict[str, Any] = {
            "server_id": it["name"],
            "server_name": d.get("server_name"),
            "description": d.get("description") or None,
            "transport": d.get("transport", "http"),
            "url": d.get("url"),
            "auth_type": d.get("auth_type"),      # explicit null clears auth on update
            "static_headers": d.get("static_headers") or None,
            "extra_headers": d.get("extra_headers") or None,
            "allowed_tools": d.get("allowed_tools") or None,
            "allow_all_keys": bool(d.get("allow_all_keys")),
            "mcp_info": d.get("mcp_info") or None,
        }
        ve = d.get("auth_value_encrypted")
        if d.get("auth_type") and ve and decrypt is not None:
            try:
                payload["credentials"] = {"auth_value": decrypt(ve)}
            except Exception as e:
                failed.append({"id": it["name"], "op": "decrypt", "error": str(e)})
                continue
        desired[it["name"]] = payload
    return desired, failed


def diff_mcp(desired: dict[str, dict], live: list[dict], changed_ids: set[str]) -> dict[str, Any]:
    """Declarative add/delete by server_id (self-healing); update only ids known changed."""
    live_ids = {s.get("server_id") for s in live if s.get("server_id")}
    desired_ids = set(desired)
    return {
        "to_add": [desired[i] for i in sorted(desired_ids - live_ids)],
        "to_update": [desired[i] for i in sorted(changed_ids & desired_ids & live_ids)],
        "to_delete": sorted(live_ids - desired_ids),
    }


async def reconcile_mcp(desired_items, live, client,
                        changed_item_names: set[str],
                        decrypt: Optional[Callable[[str], str]]) -> dict[str, Any]:
    desired, failed = build_desired(desired_items, decrypt)
    plan = diff_mcp(desired, live, changed_item_names & set(desired))
    added = updated = deleted = 0
    for entry in plan["to_add"]:
        try:
            await client.add_server(entry); added += 1
        except Exception as e:
            if _is_already_exists(e):
                try:
                    await client.update_server(entry); updated += 1
                except Exception as e2:
                    failed.append({"id": entry["server_id"], "op": "add->update", "error": str(e2)})
            else:
                failed.append({"id": entry["server_id"], "op": "add", "error": str(e)})
    for entry in plan["to_update"]:
        try:
            await client.update_server(entry); updated += 1
        except Exception as e:
            failed.append({"id": entry["server_id"], "op": "update", "error": str(e)})
    for sid in plan["to_delete"]:
        try:
            await client.delete_server(sid); deleted += 1
        except Exception as e:
            failed.append({"id": sid, "op": "delete", "error": str(e)})
    return {"added": added, "updated": updated, "deleted": deleted, "failed": failed}
```

Note: removing auth from an existing server sends `auth_type: null` but LiteLLM may inherit stored credentials on PUT (verified fact §2 of the spec). Accepted edge: the documented admin workaround is delete + re-add via the UI (two staged rows, one Apply). Do NOT try to solve this in code.

- [ ] **Step 4: Run tests** — `pytest tests/test_mcp_reconcile.py -q` → PASS; full suite green.

- [ ] **Step 5: Commit** — `feat(ui): MCP reconciler (declarative hot convergence)` + trailer.

---

### Task 4: Staging validation, encryption, redaction

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py` (imports, `_mcp_server_data`, `stage_item`, `_redact_item`)
- Test: extend `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Consumes: `_fernet()`, `effective()`, FakeStore/`_client` fixtures already in the test file.
- Produces: staged `mcp_server` items always carry the normalized data shape of spec §4 (secret as `auth_value_encrypted`); `/api/config/state` redacts the ciphertext to `***`.

- [ ] **Step 1: Write failing tests** — append to `ui/tests/test_config_v3_routes.py`:

```python
def _mcp_body(**over):
    d = {"server_name": "deepwiki", "description": "", "transport": "http",
         "url": "https://mcp.deepwiki.com/mcp", "auth_type": "", "auth_value": "",
         "static_headers": {}, "extra_headers": [], "allowed_tools": [],
         "allow_all_keys": False, "mcp_info": {}}
    d.update(over)
    return {"kind": "mcp_server", "name": "11111111-1111-1111-1111-111111111111", "data": d}

def test_stage_mcp_server_normalizes_and_encrypts(tmp_path):
    s = FakeStore(); c = _client(tmp_path, s)
    r = c.put("/api/config/item", json=_mcp_body(auth_type="bearer_token", auth_value="tok-1"))
    assert r.status_code == 200
    kind, name, data, deleted = s.staged_calls[-1]
    assert kind == "mcp_server" and deleted is False
    assert data["server_name"] == "deepwiki" and data["transport"] == "http"
    assert data["auth_value_encrypted"] == "ENC:tok-1" and "auth_value" not in data
    assert data["auth_type"] == "bearer_token" and data["allow_all_keys"] is False

def test_stage_mcp_server_rejects_bad_name_url_transport(tmp_path):
    c = _client(tmp_path, FakeStore())
    assert c.put("/api/config/item", json=_mcp_body(server_name="has space")).status_code == 422
    assert c.put("/api/config/item", json=_mcp_body(url="ftp://x")).status_code == 422
    assert c.put("/api/config/item", json=_mcp_body(transport="stdio")).status_code == 422
    assert c.put("/api/config/item", json=_mcp_body(auth_type="oauth2")).status_code == 422

def test_stage_mcp_server_auth_requires_value_when_no_existing(tmp_path):
    c = _client(tmp_path, FakeStore())
    assert c.put("/api/config/item", json=_mcp_body(auth_type="api_key", auth_value="")).status_code == 422

def test_stage_mcp_server_blank_auth_keeps_existing_ciphertext(tmp_path):
    s = FakeStore()
    s._applied.append({"kind": "mcp_server", "name": "11111111-1111-1111-1111-111111111111",
                       "data": {"server_name": "deepwiki", "transport": "http",
                                "url": "https://mcp.deepwiki.com/mcp",
                                "auth_type": "bearer_token", "auth_value_encrypted": "ENC:old"}})
    c = _client(tmp_path, s)
    r = c.put("/api/config/item", json=_mcp_body(auth_type="bearer_token", auth_value=""))
    assert r.status_code == 200
    assert s.staged_calls[-1][2]["auth_value_encrypted"] == "ENC:old"

def test_stage_mcp_server_rejects_duplicate_server_name(tmp_path):
    s = FakeStore()
    s._applied.append({"kind": "mcp_server", "name": "other-uuid",
                       "data": {"server_name": "deepwiki", "transport": "http", "url": "http://x/mcp"}})
    c = _client(tmp_path, s)
    assert c.put("/api/config/item", json=_mcp_body()).status_code == 422

def test_state_redacts_mcp_secret(tmp_path):
    s = FakeStore()
    s._applied.append({"kind": "mcp_server", "name": "u1",
                       "data": {"server_name": "fc", "transport": "http", "url": "http://x/mcp",
                                "auth_type": "api_key", "auth_value_encrypted": "ENC:secret"}})
    d = _client(tmp_path, s).get("/api/config/state").json()
    it = next(i for i in d["items"] if i["kind"] == "mcp_server")
    assert it["data"]["auth_value_encrypted"] == "***"
    assert it["data"]["url"] == "http://x/mcp"

def test_stage_mcp_server_validates_costs(tmp_path):
    c = _client(tmp_path, FakeStore())
    bad = _mcp_body(mcp_info={"mcp_server_cost_info": {"default_cost_per_query": -1}})
    assert c.put("/api/config/item", json=bad).status_code == 422
    bad2 = _mcp_body(mcp_info={"mcp_server_cost_info": {"tool_name_to_cost_per_query": {"t": "x"}}})
    assert c.put("/api/config/item", json=bad2).status_code == 422
```

- [ ] **Step 2: Run to verify failure** — new tests FAIL (staged data un-normalized / no 422s / no redaction).

- [ ] **Step 3: Implement in `ui/app/routes/config_v3_routes.py`:**

Add near the top (after imports): `import re`.

Add after `_credential_data`:

```python
_MCP_TRANSPORTS = {"http", "sse"}
_MCP_AUTH_TYPES = {"api_key", "bearer_token", "basic"}
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

def _mcp_cost_info(data: dict) -> dict:
    ci = ((data.get("mcp_info") or {}).get("mcp_server_cost_info") or {})
    out = {}
    dc = ci.get("default_cost_per_query")
    if dc is not None:
        if not isinstance(dc, (int, float)) or isinstance(dc, bool) or dc < 0:
            raise HTTPException(status_code=422, detail="default_cost_per_query must be a number >= 0")
        out["default_cost_per_query"] = float(dc)
    tools = ci.get("tool_name_to_cost_per_query") or {}
    if tools:
        clean = {}
        for t, v in tools.items():
            if not isinstance(t, str) or not t.strip() or not isinstance(v, (int, float)) \
                    or isinstance(v, bool) or v < 0:
                raise HTTPException(status_code=422, detail="tool costs must map tool name -> number >= 0")
            clean[t.strip()] = float(v)
        out["tool_name_to_cost_per_query"] = clean
    return {"mcp_server_cost_info": out} if out else {}

async def _mcp_server_data(name: str, data: dict, store) -> dict:
    """Normalize + validate an mcp_server item. A provided auth_value is Fernet-encrypted;
    BLANK auth_value with auth_type set reuses the existing ciphertext (edit without
    re-typing). config.yaml never sees these items — hot apply only."""
    data = dict(data or {})
    server_name = (data.get("server_name") or "").strip()
    if not _MCP_NAME_RE.match(server_name):
        raise HTTPException(status_code=422, detail="server_name required: letters, digits, _ or - only")
    url = (data.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="url required (http:// or https://)")
    transport = data.get("transport")
    if transport not in _MCP_TRANSPORTS:
        raise HTTPException(status_code=422, detail="transport must be http or sse")
    auth_type = (data.get("auth_type") or None)
    if auth_type is not None and auth_type not in _MCP_AUTH_TYPES:
        raise HTTPException(status_code=422, detail="auth_type must be api_key, bearer_token, basic or empty")
    eff = effective(await store.applied(), await store.staged())
    for it in eff:
        if (it["kind"] == "mcp_server" and it["name"] != name and it.get("flag") != "deleted"
                and (it.get("data") or {}).get("server_name") == server_name):
            raise HTTPException(status_code=422, detail=f"server_name {server_name!r} already in use")
    out = {
        "server_name": server_name,
        "description": (data.get("description") or "").strip(),
        "transport": transport,
        "url": url,
        "auth_type": auth_type,
        "static_headers": {str(k).strip(): str(v) for k, v in (data.get("static_headers") or {}).items()
                           if str(k).strip()},
        "extra_headers": [str(h).strip() for h in (data.get("extra_headers") or []) if str(h).strip()],
        "allowed_tools": [str(t).strip() for t in (data.get("allowed_tools") or []) if str(t).strip()],
        "allow_all_keys": bool(data.get("allow_all_keys")),
        "mcp_info": _mcp_cost_info(data),
    }
    if auth_type:
        auth_value = data.get("auth_value")
        if auth_value:
            out["auth_value_encrypted"] = _fernet().encrypt(auth_value.encode()).decode()
        else:
            existing = next((i for i in eff if i["kind"] == "mcp_server" and i["name"] == name
                             and i.get("flag") != "deleted"), None)
            ve = (existing.get("data") or {}).get("auth_value_encrypted") if existing else None
            if not ve:
                raise HTTPException(status_code=422, detail="auth_value required (no existing secret to keep)")
            out["auth_value_encrypted"] = ve
    return out
```

In `stage_item` (after the credential branch):

```python
    if kind == "mcp_server":
        data = await _mcp_server_data(name, data, make_config_store())
```

Extend `_redact_item`:

```python
def _redact_item(it: dict) -> dict:
    if it["kind"] == "credential":
        d = it["data"] or {}
        return {**it, "data": {"provider": d.get("provider"), "api_key": "***"}}
    if it["kind"] == "mcp_server":
        d = dict(it["data"] or {})
        if d.get("auth_value_encrypted"):
            d["auth_value_encrypted"] = "***"
        return {**it, "data": d}
    return it
```

(`/api/config/export` intentionally keeps the ciphertext — backup parity with `credential` items; no change.)

- [ ] **Step 4: Run** — `pytest tests/test_config_v3_routes.py -q` → PASS; full suite green.
- [ ] **Step 5: Commit** — `feat(ui): stage/validate/redact mcp_server config items` + trailer.

---

### Task 5: Engine wiring (hot apply) + apply endpoint + apply-report UI

**Files:**
- Modify: `ui/app/config_engine.py`, `ui/app/config_render.py`, `ui/app/routes/config_v3_routes.py`, `ui/frontend/src/lib/configStore.svelte.js`
- Test: extend `ui/tests/test_config_engine.py`

**Interfaces:**
- Consumes: `reconcile_mcp` (Task 3), `McpClient` (Task 2), existing FakeStore/FakeReloader test fakes.
- Produces: `apply_config(..., mcp_client=None, ...)` new keyword; hybrid apply result gains `"mcp": {added, updated, deleted, failed}`; `make_mcp_client()` factory in `config_v3_routes.py` (monkeypatch seam, used again by Tasks 6 and 8's pattern).

- [ ] **Step 1: Write failing tests** — append to `ui/tests/test_config_engine.py`:

```python
# --- v3.27: MCP hot apply ---

class McpStore(FakeStore):
    def __init__(self):
        super().__init__()
        self._applied = []
        self._staged = [{"kind": "mcp_server", "name": "u1", "flag": "new",
                         "data": {"server_name": "deepwiki", "transport": "http",
                                  "url": "https://mcp.deepwiki.com/mcp"}}]


class FakeMcpClient:
    def __init__(self):
        self.listed = 0; self.added = []
    async def list_servers(self): self.listed += 1; return []
    async def add_server(self, p): self.added.append(p["server_id"])
    async def update_server(self, p): pass
    async def delete_server(self, sid): pass


class FakeModelsClient:
    async def list_models(self): return []


@pytest.mark.asyncio
async def test_hybrid_apply_reconciles_mcp_without_restart(tmp_path):
    store = McpStore(); rl = FakeReloader(ok=True); mc = FakeMcpClient()
    res = await apply_config(str(tmp_path / "c.yaml"), store, rl, decrypt=lambda b: b,
                             models_client=FakeModelsClient(), mcp_client=mc, hybrid=True)
    assert res["applied"] is True and res["restart"] == "skipped"
    assert rl.calls == 0                       # MCP changes never restart the proxy
    assert mc.added == ["u1"]
    assert res["mcp"] == {"added": 1, "updated": 0, "deleted": 0, "failed": []}
    assert store.folded is True


@pytest.mark.asyncio
async def test_hybrid_apply_mcp_list_failure_reported_not_raised(tmp_path):
    class BadMcp(FakeMcpClient):
        async def list_servers(self): raise RuntimeError("down")
    store = McpStore(); rl = FakeReloader(ok=True)
    res = await apply_config(str(tmp_path / "c.yaml"), store, rl, decrypt=lambda b: b,
                             models_client=FakeModelsClient(), mcp_client=BadMcp(), hybrid=True)
    assert res["applied"] is True and res["mcp"]["failed"][0]["op"] == "list"


@pytest.mark.asyncio
async def test_non_hybrid_apply_rejects_mcp_items(tmp_path):
    store = McpStore(); rl = FakeReloader(ok=True)
    with pytest.raises(ApplyError) as ei:
        await apply_config(str(tmp_path / "c.yaml"), store, rl, decrypt=lambda b: b)
    assert "invalid" in str(ei.value).lower() and "mcp" in str(ei.value).lower()
    assert store.folded is False
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_config_engine.py -q` → new tests FAIL (`unexpected keyword 'mcp_client'`).

- [ ] **Step 3: Implement:**

`ui/app/config_engine.py` — add import `from app.mcp_reconcile import reconcile_mcp`; extend signature:

```python
async def apply_config(config_path, store, reloader, *, decrypt, models_client=None,
                       mcp_client=None, hybrid=False) -> dict:
```

Immediately after the integrity gate (before `if not hybrid:`):

```python
    if not hybrid and any(s["kind"] == "mcp_server" for s in staged):
        raise ApplyError("invalid config, not applied: mcp_server items require hybrid mode "
                         "(STORE_MODEL_IN_DB=true) — MCP servers hot-apply and are never rendered")
```

In the hybrid flow, replace `out = {"applied": True, "hybrid": True, "models": model_report}` with:

```python
    out = {"applied": True, "hybrid": True, "models": model_report}
    if mcp_client is not None:
        # post-commit, reported never rolled back — mirrors the model reconcile.
        # Runs on EVERY hybrid apply (declarative self-healing, same as models):
        # servers created directly in LiteLLM get removed; the master is authoritative.
        mcp_changed = {s["name"] for s in staged
                       if s["kind"] == "mcp_server" and s.get("flag") in ("new", "changed")}
        mcp_items = [it for it in eff if it["kind"] == "mcp_server" and it.get("flag") != "deleted"]
        try:
            mcp_live = await mcp_client.list_servers()
            out["mcp"] = await reconcile_mcp(mcp_items, mcp_live, mcp_client, mcp_changed, decrypt)
        except Exception as e:
            out["mcp"] = {"added": 0, "updated": 0, "deleted": 0,
                          "failed": [{"id": "*", "op": "list", "error": str(e)}]}
```

`ui/app/config_render.py` — in `render_config`, after the `elif kind in _SECTION_BY_KIND:` branch add:

```python
        elif kind == "mcp_server":
            pass  # DB-only entity: hot-applied via /v1/mcp/server (mcp_reconcile); never rendered
```

`ui/app/routes/config_v3_routes.py` — add import `from app.mcp_client import McpClient`, factory after `make_models_client`:

```python
def make_mcp_client() -> McpClient:
    s = get_settings()
    return McpClient(s.litellm_base_url, s.litellm_master_key)
```

and in `apply()` pass it through:

```python
            models_client=make_models_client() if s.store_model_in_db else None,
            mcp_client=make_mcp_client() if s.store_model_in_db else None,
            hybrid=s.store_model_in_db,
```

`ui/frontend/src/lib/configStore.svelte.js` — in `apply()`, replace the hybrid branch body with:

```js
      if (r.hybrid) {
        const m = r.models || {}
        const mc = r.mcp
        let msg = `Applied live — ${m.added || 0} added, ${m.updated || 0} updated, ${m.deleted || 0} deleted`
        if (mc) msg += `; MCP — ${mc.added || 0} added, ${mc.updated || 0} updated, ${mc.deleted || 0} deleted`
        if (r.restart === 'healthy') msg += '; settings change restarted the proxy (healthy)'
        else if (r.restart === 'unhealthy') msg += `; settings restart UNHEALTHY: ${r.detail || ''}`
        const allFailed = [...(m.failed || []).map(f => `${f.id} (${f.op})`),
                           ...((mc && mc.failed) || []).map(f => `MCP ${f.id} (${f.op})`)]
        if (allFailed.length) {
          error = `${msg}. ${allFailed.length} op(s) failed: ${allFailed.join(', ')}`
          notice = ''
        } else {
          notice = msg
        }
      } else {
```

- [ ] **Step 4: Run** — `pytest tests/ -q` all green; `cd ui/frontend && npm run build` clean.
- [ ] **Step 5: Commit** — `feat(ui): hot-apply mcp_server items via MCP reconciler` + trailer.

---

### Task 6: Drift + resync coverage

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py` (`config_drift`, `config_resync`)
- Test: extend `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Consumes: `build_desired`/`mcp_content_diff`/`reconcile_mcp` (Task 3), `make_mcp_client` (Task 5).
- Produces: `GET /api/config/drift` response gains top-level `"mcp": {missing_in_litellm, extra_in_litellm, content_drifted}` (or `{error, detail}` on query failure); `POST /api/config/resync` response keeps its existing top-level model fields and gains `"mcp": <report>`. Existing `in_sync` stays models-only (Models.svelte badge semantics unchanged); the MCP page computes its own from `drift.mcp` (Task 10).

- [ ] **Step 1: Write failing tests** — append to `ui/tests/test_config_v3_routes.py`:

```python
class _McpFakeClient:
    def __init__(self, live):
        self._live = live
        self.updated, self.added, self.deleted = [], [], []
    async def list_servers(self): return list(self._live)
    async def add_server(self, p): self.added.append(p["server_id"])
    async def update_server(self, p): self.updated.append(p["server_id"])
    async def delete_server(self, sid): self.deleted.append(sid)

def _mcp_applied_store():
    s = FakeStore()
    s._applied = [{"kind": "mcp_server", "name": "u1",
                   "data": {"server_name": "deepwiki", "transport": "http",
                            "url": "https://mcp.deepwiki.com/mcp"}}]
    s._staged = []
    return s

def test_drift_reports_mcp_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings; get_settings.cache_clear()
    s = _mcp_applied_store(); c = _client(tmp_path, s)
    import app.routes.config_v3_routes as cr
    class _NoModels:
        async def list_models(self): return []
    cr.make_models_client = lambda: _NoModels()
    live = [{"server_id": "u1", "server_name": "deepwiki", "transport": "http",
             "url": "http://WRONG/mcp"},
            {"server_id": "ghost", "server_name": "ghost"}]
    cr.make_mcp_client = lambda: _McpFakeClient(live)
    d = c.get("/api/config/drift").json()
    m = d["mcp"]
    assert m["missing_in_litellm"] == []
    assert [e["id"] for e in m["extra_in_litellm"]] == ["ghost"]
    assert m["content_drifted"][0]["id"] == "u1" and "url" in m["content_drifted"][0]["fields"]
    get_settings.cache_clear()

def test_resync_converges_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_MODEL_IN_DB", "true")
    from app.settings import get_settings; get_settings.cache_clear()
    s = _mcp_applied_store(); c = _client(tmp_path, s)
    import app.routes.config_v3_routes as cr
    class _NoModels:
        async def list_models(self): return []
    cr.make_models_client = lambda: _NoModels()
    mc = _McpFakeClient([{"server_id": "u1", "server_name": "deepwiki", "transport": "http",
                          "url": "http://WRONG/mcp"}, {"server_id": "ghost"}])
    cr.make_mcp_client = lambda: mc
    r = c.post("/api/config/resync").json()
    assert r["mcp"]["updated"] == 1 and r["mcp"]["deleted"] == 1
    assert mc.updated == ["u1"] and mc.deleted == ["ghost"]
    get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify failure** — drift response has no `mcp` key → FAIL.

- [ ] **Step 3: Implement in `config_v3_routes.py`:**

Add import: `from app.mcp_reconcile import build_desired as build_mcp_desired, mcp_content_diff, reconcile_mcp`.

In `config_drift()`, change `model_items = [...]` to read applied once, and append the MCP section just before the return:

```python
    applied_items = await store.applied()
    model_items = [it for it in applied_items if it["kind"] == "model"]
    ...  # existing model drift code unchanged
    mcp_items = [it for it in applied_items if it["kind"] == "mcp_server"]
    try:
        mcp_live = await make_mcp_client().list_servers()
    except Exception as e:
        mcp_out = {"error": "query_failed", "detail": str(e)}
    else:
        desired_mcp, _ = build_mcp_desired(mcp_items, None)
        live_by_id = {s.get("server_id"): s for s in mcp_live if s.get("server_id")}
        mcp_out = {
            "missing_in_litellm": [{"id": i, "server_name": desired_mcp[i].get("server_name")}
                                   for i in sorted(set(desired_mcp) - set(live_by_id))],
            "extra_in_litellm": [{"id": i, "server_name": (live_by_id[i] or {}).get("server_name")}
                                 for i in sorted(set(live_by_id) - set(desired_mcp))],
            "content_drifted": [],
        }
        for sid in sorted(set(desired_mcp) & set(live_by_id)):
            fields = mcp_content_diff(desired_mcp[sid], live_by_id[sid])
            if fields:
                mcp_out["content_drifted"].append(
                    {"id": sid, "server_name": desired_mcp[sid].get("server_name"), "fields": fields})
    return {"hybrid": True, "in_sync": not missing and not extra and not content,
            "missing_in_litellm": missing, "extra_in_litellm": extra, "content_drifted": content,
            "mcp": mcp_out}
```

In `config_resync()`, before the final return, capture the model report and extend:

```python
    model_report = await reconcile_models(model_items, live, client,
                                          changed_item_names=set(), creds_changed=set(),
                                          resolve_key=resolve_key, converge_content=True)
    dec = lambda b: f.decrypt(b.encode()).decode()
    mcp_items = [it for it in applied if it["kind"] == "mcp_server"]
    mcp_client = make_mcp_client()
    try:
        mcp_live = await mcp_client.list_servers()
        desired_mcp, _ = build_mcp_desired(mcp_items, None)
        live_by_id = {s.get("server_id"): s for s in mcp_live if s.get("server_id")}
        drifted = {sid for sid in (set(desired_mcp) & set(live_by_id))
                   if mcp_content_diff(desired_mcp[sid], live_by_id[sid])}
        mcp_report = await reconcile_mcp(mcp_items, mcp_live, mcp_client, drifted, dec)
    except Exception as e:
        mcp_report = {"added": 0, "updated": 0, "deleted": 0,
                      "failed": [{"id": "*", "op": "list", "error": str(e)}]}
    return {**model_report, "mcp": mcp_report}
```

Content-drift caveat: if the local e2e (Task 13) shows false drift because LiteLLM's list response decorates fields we manage (e.g. fills `mcp_info` defaults), remove the offending field from `MCP_MANAGED_FIELDS` and note it there — presence (add/delete) must stay.

- [ ] **Step 4: Run** — targeted then full suite green.
- [ ] **Step 5: Commit** — `feat(ui): drift + resync cover MCP servers` + trailer.

---

### Task 7: Bootstrap import, integrity, key-reference validation

**Files:**
- Modify: `ui/app/config_import.py`, `ui/app/config_integrity.py`, `ui/app/routes/keys_routes.py`, `ui/app/routes/config_v3_routes.py` (`config_integrity`, `config_integrity_fix`)
- Test: extend `ui/tests/test_config_import.py`, `ui/tests/test_config_integrity.py`, `ui/tests/test_keys_routes.py`

**Interfaces:**
- Consumes: existing `split_config(cfg, encrypt)`, `_missing`/`_orphan` helpers, `_validate_key_refs` fixtures in `test_keys_routes.py` (FakeStore-style config store monkeypatch — mirror the existing tests in that file).
- Produces: `mcp_server_names(items) -> set` and `key_mcp_orphans(keys, valid) -> list[dict]` in `config_integrity.py`; `split_config` imports `mcp_servers:` blocks; key create/update 422s on unknown MCP grants; `/api/config/integrity` response gains `key_mcp_orphans` (and folds it into `in_sync`); `/api/config/integrity/fix` handles `field == "mcp_servers"`.

- [ ] **Step 1: Write failing tests.**

`ui/tests/test_config_import.py` — append:

```python
def test_split_config_imports_mcp_servers():
    from app.config_import import split_config
    cfg = {"model_list": [], "mcp_servers": {
        "deepwiki": {"url": "https://mcp.deepwiki.com/mcp", "transport": "http"},
        "fc": {"url": "http://10.0.20.9:3002/mcp", "auth_type": "bearer_token", "auth_value": "tok"},
    }}
    items, passthrough = split_config(cfg, encrypt=lambda s: "ENC:" + s)
    mcp = {i["data"]["server_name"]: i for i in items if i["kind"] == "mcp_server"}
    assert set(mcp) == {"deepwiki", "fc"}
    assert mcp["deepwiki"]["data"]["transport"] == "http"
    assert mcp["fc"]["data"]["auth_value_encrypted"] == "ENC:tok"
    assert "auth_value" not in mcp["fc"]["data"]
    assert "mcp_servers" not in passthrough
```

`ui/tests/test_config_integrity.py` — append:

```python
def test_mcp_server_names_includes_uuid_and_name():
    from app.config_integrity import mcp_server_names
    items = [{"kind": "mcp_server", "name": "u1", "data": {"server_name": "deepwiki"}},
             {"kind": "mcp_server", "name": "u2", "data": {"server_name": "gone"}, "flag": "deleted"},
             {"kind": "model", "name": "m1", "data": {}}]
    assert mcp_server_names(items) == {"u1", "deepwiki"}


def test_key_mcp_orphans():
    from app.config_integrity import key_mcp_orphans
    keys = [{"token": "t1", "key_alias": "a", "object_permission": {"mcp_servers": ["u1", "dead"]}},
            {"token": "t2", "key_alias": "b"}]
    out = key_mcp_orphans(keys, {"u1"})
    assert len(out) == 1 and out[0]["reference"] == "dead" and out[0]["target"]["field"] == "mcp_servers"


def test_trim_key_field_mcp_servers():
    from app.config_integrity import trim_key_field
    assert trim_key_field(["u1", "dead"], {"field": "mcp_servers", "entry": "dead"}) == ["u1"]
```

`ui/tests/test_keys_routes.py` — append (extends that file's existing `FakeConfigStore`/`_client_v` fixtures):

```python
class FakeConfigStoreMcp(FakeConfigStore):
    def __init__(self, groups, mcp):
        super().__init__(groups)
        self._items += [{"kind": "mcp_server", "name": name, "data": {"server_name": sn}}
                        for name, sn in mcp]


def _client_mcp(tmp_path, fake, groups, mcp):
    os.environ["DATABASE_URL"] = "fake://test"  # enable validation
    c = _client(tmp_path, fake, clear_db_url=False)
    import app.routes.keys_routes as kr
    kr.make_config_store = lambda: FakeConfigStoreMcp(groups, mcp)
    return c


def test_create_key_rejects_unknown_mcp_grant(tmp_path):
    c = _client_mcp(tmp_path, FakeKeys(), groups=["g1"], mcp=[("u1", "deepwiki")])
    r = c.post("/api/keys", json={"key_alias": "x", "object_permission": {"mcp_servers": ["nope"]}})
    assert r.status_code == 422
    assert "unknown MCP server" in r.json()["detail"] and "nope" in r.json()["detail"]


def test_create_key_accepts_mcp_grant_by_uuid_or_name(tmp_path):
    c = _client_mcp(tmp_path, FakeKeys(), groups=["g1"], mcp=[("u1", "deepwiki")])
    r = c.post("/api/keys", json={"key_alias": "x",
                                  "object_permission": {"mcp_servers": ["u1", "deepwiki"]}})
    assert r.status_code == 200
    # FakeKeys echoes the payload — grants forwarded verbatim, untouched
    assert r.json()["object_permission"] == {"mcp_servers": ["u1", "deepwiki"]}


def test_update_key_rejects_unknown_mcp_grant(tmp_path):
    c = _client_mcp(tmp_path, FakeKeys(), groups=["g1"], mcp=[("u1", "deepwiki")])
    r = c.post("/api/keys/update", json={"key": "h1", "object_permission": {"mcp_servers": ["dead"]}})
    assert r.status_code == 422 and "dead" in r.json()["detail"]
```

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement.**

`ui/app/config_import.py`:

```python
_KNOWN = {"model_list", "router_settings", "litellm_settings", "general_settings",
          "credential_list", "mcp_servers"}
```

and in `split_config`, before the `passthrough` line:

```python
    for sname, sconf in (cfg.get("mcp_servers") or {}).items():
        sconf = dict(sconf or {})
        auth_value = sconf.pop("auth_value", None)
        data = {"server_name": sname,
                "description": sconf.get("description") or "",
                "transport": sconf.get("transport", "http"),
                "url": sconf.get("url"),
                "auth_type": sconf.get("auth_type"),
                "static_headers": sconf.get("static_headers") or {},
                "extra_headers": sconf.get("extra_headers") or [],
                "allowed_tools": sconf.get("allowed_tools") or [],
                "allow_all_keys": bool(sconf.get("allow_all_keys")),
                "mcp_info": sconf.get("mcp_info") or {}}
        if auth_value:
            data["auth_value_encrypted"] = encrypt(auth_value)
        items.append({"kind": "mcp_server", "name": str(uuid.uuid4()), "data": data})
```

(Import is verbatim — no validation. Bootstrap import lands on a fresh DB; anything odd is reviewed/edited in the UI before the first Apply, and a bad entry fails per-server in the apply report, not silently.)

`ui/app/config_integrity.py` — append:

```python
def mcp_server_names(items: list[dict]) -> set:
    """Valid MCP references a key may hold: the item uuid (server_id) and server_name."""
    out = set()
    for it in items or []:
        if it.get("kind") != "mcp_server" or it.get("flag") == "deleted":
            continue
        out.add(it.get("name"))
        sn = (it.get("data") or {}).get("server_name")
        if sn:
            out.add(sn)
    return out


def key_mcp_orphans(keys: list[dict], valid: set) -> list[dict]:
    """Keys whose object_permission.mcp_servers name servers absent from the config."""
    out: list[dict] = []
    for k in keys or []:
        token = k.get("token")
        label = k.get("key_alias") or (token or "")[:10]
        op = k.get("object_permission") if isinstance(k.get("object_permission"), dict) else {}
        for sid in (op.get("mcp_servers") or []):
            if _missing(sid, valid):
                out.append(_orphan("key", f"key '{label}' → MCP servers", sid,
                                   {"token": token, "field": "mcp_servers", "entry": sid}))
    return out
```

and extend `trim_key_field`:

```python
def trim_key_field(value, target: dict):
    """Return the key's models list / aliases dict / mcp grant list minus the dead entry."""
    if target["field"] in ("models", "mcp_servers"):
        return [m for m in (value or []) if m != target["entry"]]
    return {a: t for a, t in (value or {}).items() if a != target["entry"]}
```

`ui/app/routes/keys_routes.py` — import `mcp_server_names` from `app.config_integrity`; at the end of `_validate_key_refs` (it already has `eff` computed):

```python
    op = payload.get("object_permission")
    if isinstance(op, dict):
        valid = mcp_server_names([i for i in eff if i["kind"] == "mcp_server"])
        bad_mcp = [s for s in (op.get("mcp_servers") or [])
                   if isinstance(s, str) and s and s not in valid]
        if bad_mcp:
            raise HTTPException(status_code=422,
                                detail=f"key references unknown MCP server(s): {', '.join(sorted(set(bad_mcp)))}")
```

`ui/app/routes/config_v3_routes.py` — `config_integrity()`: import `key_mcp_orphans, mcp_server_names` (extend the existing `config_integrity` import line); compute and return:

```python
    mcp_valid = mcp_server_names([i for i in eff if i["kind"] == "mcp_server"])
    k_mcp = key_mcp_orphans(keys, mcp_valid)
    return {"in_sync": not r_orphans and not k_orphans and not k_mcp,
            "router_orphans": r_orphans, "key_orphans": k_orphans, "key_mcp_orphans": k_mcp}
```

(also add `"key_mcp_orphans": []` to the early `query_failed` return). `config_integrity_fix()` `scope == "key"` branch — the mcp field nests under `object_permission`:

```python
        field = target["field"]
        if field == "mcp_servers":
            op = k.get("object_permission") if isinstance(k.get("object_permission"), dict) else {}
            before = op.get("mcp_servers")
            after = trim_key_field(before, target)
            if dry:
                return {"before": before, "after": after, "effect": "applies immediately (hot)"}
            await make_keys_client().update_key({"key": target["token"],
                                                 "object_permission": {"mcp_servers": after}})
            return {"applied": True, "needs_apply": False}
        before = k.get(field)
        ...  # existing models/aliases path unchanged
```

- [ ] **Step 4: Run** — all three test files pass; full suite green.
- [ ] **Step 5: Commit** — `feat(ui): MCP import, integrity and key-grant validation` + trailer.

---

### Task 8: `/api/mcp/*` routes

**Files:**
- Create: `ui/app/routes/mcp_routes.py`
- Modify: `ui/app/main.py` (import + `app.include_router(mcp_routes.router)` after `models_routes`)
- Test: `ui/tests/test_mcp_routes.py`

**Interfaces:**
- Consumes: `McpClient` (Task 2), `login_required`, `_iso_utc` from `app.routes.usage_routes`.
- Produces (consumed by frontend Tasks 10):
  - `GET /api/mcp/health?probe=0&server_ids=a,b` → `{"servers": [{server_id, server_name, status, last_health_check, health_check_error}], "probe"?: <raw LiteLLM health response>, "probe_error"?: str}` (probe=1 additionally calls the live health endpoint)
  - `GET /api/mcp/tools?server_id=` → LiteLLM's tools/list response passed through
  - `GET /api/mcp/usage?days=30` → `{"rows": [{server, calls, spend, failures, last_call}], "error"?: "query_failed"}`

- [ ] **Step 1: Write failing tests** — `ui/tests/test_mcp_routes.py` (login fixture pattern copied from `test_config_v3_routes.py::_client`, monkeypatching `app.routes.mcp_routes.make_mcp_client`):

```python
import os
from fastapi.testclient import TestClient
from app.auth import hash_password


class FakeMcp:
    def __init__(self):
        self.health_called_with = None
    async def list_servers(self):
        return [{"server_id": "u1", "server_name": "deepwiki", "status": "healthy",
                 "last_health_check": "2026-08-13T00:00:00", "health_check_error": None,
                 "url": "x", "credentials": None}]
    async def health(self, ids):
        self.health_called_with = ids
        return {"u1": {"status": "healthy"}}
    async def list_tools(self, server_id):
        return {"tools": [{"name": "read_wiki_structure", "description": "d"}]}


def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path / "c.yaml"), DATABASE_URL="")
    (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.mcp_routes as mr
    mr.make_mcp_client = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


def test_health_requires_login(tmp_path):
    c = _client(tmp_path, FakeMcp()); c.cookies.clear()
    assert c.get("/api/mcp/health").status_code == 401


def test_health_lists_persisted_status(tmp_path):
    d = _client(tmp_path, FakeMcp()).get("/api/mcp/health").json()
    assert d["servers"][0]["server_id"] == "u1" and d["servers"][0]["status"] == "healthy"
    assert "probe" not in d
    assert "credentials" not in d["servers"][0] and "url" not in d["servers"][0]


def test_health_probe_calls_litellm(tmp_path):
    fake = FakeMcp()
    d = _client(tmp_path, fake).get("/api/mcp/health?probe=1&server_ids=u1").json()
    assert fake.health_called_with == ["u1"] and d["probe"] == {"u1": {"status": "healthy"}}


def test_tools_passthrough(tmp_path):
    d = _client(tmp_path, FakeMcp()).get("/api/mcp/tools?server_id=u1").json()
    assert d["tools"][0]["name"] == "read_wiki_structure"


def test_tools_requires_server_id(tmp_path):
    assert _client(tmp_path, FakeMcp()).get("/api/mcp/tools").status_code == 422


def test_usage_no_dsn_returns_empty(tmp_path):
    d = _client(tmp_path, FakeMcp()).get("/api/mcp/usage").json()
    assert d == {"rows": []}
```

- [ ] **Step 2: Run to verify failure** — module missing.

- [ ] **Step 3: Implement** — `ui/app/routes/mcp_routes.py`:

```python
import logging
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import login_required
from app.settings import get_settings
from app.mcp_client import McpClient
from app.routes.usage_routes import _iso_utc

router = APIRouter(prefix="/api")
log = logging.getLogger("uvicorn.error")


def make_mcp_client() -> McpClient:
    s = get_settings()
    return McpClient(s.litellm_base_url, s.litellm_master_key)


@router.get("/mcp/health", dependencies=[Depends(login_required)])
async def mcp_health(probe: int = 0, server_ids: str = ""):
    """Persisted per-server health (cheap, from the LiteLLM server table via list),
    plus an optional live probe (probe=1) which actively contacts each MCP server."""
    client = make_mcp_client()
    ids = [s for s in server_ids.split(",") if s] or None
    try:
        servers = await client.list_servers()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy MCP API error: {e}")
    out = {"servers": [{"server_id": s.get("server_id"), "server_name": s.get("server_name"),
                        "status": s.get("status"),
                        "last_health_check": _iso_utc_maybe(s.get("last_health_check")),
                        "health_check_error": s.get("health_check_error")}
                       for s in servers if ids is None or s.get("server_id") in ids]}
    if probe:
        try:
            out["probe"] = await client.health(ids)
        except Exception as e:
            out["probe_error"] = str(e)
    return out


def _iso_utc_maybe(v):
    """list_servers timestamps may arrive as ISO strings already — pass those through."""
    if v is None or isinstance(v, str):
        return v
    return _iso_utc(v)


@router.get("/mcp/tools", dependencies=[Depends(login_required)])
async def mcp_tools(server_id: str = Query(...)):
    try:
        return await make_mcp_client().list_tools(server_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy MCP API error: {e}")


@router.get("/mcp/usage", dependencies=[Depends(login_required)])
async def mcp_usage(days: int = 30):
    days = max(1, min(int(days), 365))
    dsn = get_settings().database_url
    if not dsn:
        return {"rows": []}
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT COALESCE(l.metadata::jsonb #>> '{mcp_tool_call_metadata,mcp_server_name}', '(unknown)') server, "
            "COUNT(*) calls, COALESCE(SUM(l.spend),0) spend, "
            "COUNT(*) FILTER (WHERE l.status='failure') failures, "
            'MAX(l."startTime") last_call '
            'FROM "LiteLLM_SpendLogs" l '
            "WHERE l.call_type = 'call_mcp_tool' "
            'AND l."startTime" > now() - make_interval(days => $1) '
            "GROUP BY server ORDER BY calls DESC", days)
    except Exception:
        log.exception("mcp_usage query failed (days=%s)", days)
        return {"rows": [], "error": "query_failed"}
    finally:
        await conn.close()
    return {"rows": [{"server": r["server"], "calls": r["calls"], "spend": float(r["spend"] or 0),
                      "failures": r["failures"], "last_call": _iso_utc(r["last_call"])} for r in rows]}
```

`ui/app/main.py`: add `mcp_routes` to the `from app.routes import ...` (or matching import style used there) and `app.include_router(mcp_routes.router)` after `models_routes`.

- [ ] **Step 4: Run** — `pytest tests/test_mcp_routes.py -q` PASS; full suite green.
- [ ] **Step 5: Commit** — `feat(ui): /api/mcp health, tools and usage endpoints` + trailer.

---

### Task 9: `lib/mcp.js` + api.js entries

**Files:**
- Create: `ui/frontend/src/lib/mcp.js`
- Modify: `ui/frontend/src/lib/api.js`
- Test: node module assertions (documented command below; no test framework in this repo)

**Interfaces:**
- Produces (consumed by Task 10): `headerRowsToDict([{k,v}])→{}`, `dictToHeaderRows({})→[{k,v}]`, `costRowsToDict([{tool,cost}])→{}`, `dictToCostRows({})→[{tool,cost}]`, `listRowsToArray(string[])→string[]` (trim/dedup), `arrayToListRows(any)→string[]`, `buildMcpInfo(defaultCost, costRows)→{}|{mcp_server_cost_info:{...}}`, `validateMcpForm(form)→string|null` (`form` needs `server_name,url,auth_type,auth_value,hasStoredSecret`); api additions `mcpHealth(probe, serverIds)`, `mcpTools(serverId)`, `mcpUsage(days)`.

- [ ] **Step 1: Implement** — `ui/frontend/src/lib/mcp.js`:

```js
// MCP server form helpers: convert between UI rows and the stored item shape.
// Pure functions — node-testable without Svelte.

export function headerRowsToDict(rows) {
  const out = {}
  for (const r of rows || []) {
    const k = (r?.k ?? '').trim()
    if (k && !(k in out)) out[k] = (r?.v ?? '').trim()
  }
  return out
}

export function dictToHeaderRows(obj) {
  return Object.entries(obj || {}).map(([k, v]) => ({ k, v: String(v ?? '') }))
}

export function costRowsToDict(rows) {
  const out = {}
  for (const r of rows || []) {
    const t = (r?.tool ?? '').trim()
    const c = Number(r?.cost)
    if (t && Number.isFinite(c) && c >= 0 && !(t in out)) out[t] = c
  }
  return out
}

export function dictToCostRows(obj) {
  return Object.entries(obj || {}).map(([tool, cost]) => ({ tool, cost: String(cost) }))
}

export function listRowsToArray(rows) {
  const out = []
  for (const r of rows || []) {
    const v = (typeof r === 'string' ? r : '').trim()
    if (v && !out.includes(v)) out.push(v)
  }
  return out
}

export function arrayToListRows(value) {
  if (!Array.isArray(value)) return []
  return value.filter(v => typeof v === 'string' && v.trim()).map(v => v.trim())
}

export function buildMcpInfo(defaultCost, costRows) {
  const ci = {}
  const dc = Number(defaultCost)
  if (defaultCost !== '' && defaultCost != null && Number.isFinite(dc) && dc >= 0) ci.default_cost_per_query = dc
  const costs = costRowsToDict(costRows)
  if (Object.keys(costs).length) ci.tool_name_to_cost_per_query = costs
  return Object.keys(ci).length ? { mcp_server_cost_info: ci } : {}
}

export function validateMcpForm(f) {
  if (!/^[A-Za-z0-9_-]+$/.test((f.server_name || '').trim())) return 'Server name is required (letters, digits, _ or - only).'
  if (!/^https?:\/\//.test((f.url || '').trim())) return 'URL is required (http:// or https://).'
  if (f.auth_type && !(f.auth_value || '').trim() && !f.hasStoredSecret) return 'Auth value is required for the selected auth type.'
  return null
}
```

`ui/frontend/src/lib/api.js` — add before the closing brace of the `api` object:

```js
  mcpHealth: (probe = 0, serverIds = '') => req(`/api/mcp/health?probe=${probe ? 1 : 0}${serverIds ? `&server_ids=${encodeURIComponent(serverIds)}` : ''}`),
  mcpTools: (serverId) => req(`/api/mcp/tools?server_id=${encodeURIComponent(serverId)}`),
  mcpUsage: (days = 30) => req(`/api/mcp/usage?days=${days}`),
```

- [ ] **Step 2: Node assertions** — run from `ui/frontend`:

```bash
node --input-type=module -e "
import { headerRowsToDict, dictToHeaderRows, costRowsToDict, listRowsToArray, buildMcpInfo, validateMcpForm } from './src/lib/mcp.js'
import assert from 'node:assert'
assert.deepEqual(headerRowsToDict([{k:' X-A ',v:' 1 '},{k:'',v:'z'},{k:'X-A',v:'2'}]), {'X-A':'1'})
assert.deepEqual(dictToHeaderRows({a:1}), [{k:'a',v:'1'}])
assert.deepEqual(costRowsToDict([{tool:'t',cost:'0.05'},{tool:'',cost:'1'},{tool:'u',cost:'-1'}]), {t:0.05})
assert.deepEqual(listRowsToArray([' a ','','a','b']), ['a','b'])
assert.deepEqual(buildMcpInfo('', []), {})
assert.deepEqual(buildMcpInfo('0.01', [{tool:'t',cost:'2'}]), {mcp_server_cost_info:{default_cost_per_query:0.01, tool_name_to_cost_per_query:{t:2}}})
assert.equal(validateMcpForm({server_name:'ok', url:'https://x/mcp', auth_type:'', auth_value:''}), null)
assert.match(validateMcpForm({server_name:'bad name', url:'https://x'}), /Server name/)
assert.match(validateMcpForm({server_name:'ok', url:'x'}), /URL/)
assert.match(validateMcpForm({server_name:'ok', url:'https://x', auth_type:'api_key', auth_value:'', hasStoredSecret:false}), /Auth value/)
assert.equal(validateMcpForm({server_name:'ok', url:'https://x', auth_type:'api_key', auth_value:'', hasStoredSecret:true}), null)
console.log('mcp.js OK')
"
```
Expected: `mcp.js OK`.

- [ ] **Step 3: Build** — `npm run build` clean.
- [ ] **Step 4: Commit** — `feat(ui): mcp.js form helpers + api endpoints` + trailer.

---

### Task 10: MCP Servers page

**Files:**
- Create: `ui/frontend/src/routes/McpServers.svelte`
- Modify: `ui/frontend/src/App.svelte`

**Interfaces:**
- Consumes: `store` prop (configStore), `api.mcpHealth/mcpTools/mcpUsage/drift/resync`, `lib/mcp.js` (Task 9), `money`/`fmtDateTime` from `lib/format.js`, `uuidv4` from `lib/browser.js`.
- Produces: screen key `mcp`.

- [ ] **Step 1: `App.svelte`** — add import `import McpServers from './routes/McpServers.svelte'`; nav button after Caching (line ~52):

```svelte
      <button class="nav" class:active={screen==='mcp'} onclick={() => screen='mcp'}>🧰 MCP Servers</button>
```

and a render branch after `caching`:

```svelte
      {:else if screen==='mcp'}<McpServers {store} />
```

- [ ] **Step 2: Create `ui/frontend/src/routes/McpServers.svelte`:**

```svelte
<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uuidv4 } from '../lib/browser.js'
  import { money, fmtDateTime } from '../lib/format.js'
  import { headerRowsToDict, dictToHeaderRows, costRowsToDict, dictToCostRows,
           listRowsToArray, arrayToListRows, buildMcpInfo, validateMcpForm } from '../lib/mcp.js'
  let { store } = $props()

  let showAdd = $state(false)
  let editingId = $state(null)
  let formErr = $state('')
  let form = $state({ server_name: '', description: '', transport: 'http', url: '',
                      auth_type: '', auth_value: '', allow_all_keys: false,
                      default_cost: '', hasStoredSecret: false })
  let headerRows = $state([])   // static headers [{k,v}]
  let extraRows = $state([])    // forwarded header names, string[]
  let toolRows = $state([])     // allowed tools, string[]
  let costRows = $state([])     // per-tool costs [{tool,cost}]

  let mcpItems = $derived(store.itemsOfKind('mcp_server'))

  let healthMap = $state({})    // server_id → {status,last_health_check,health_check_error}
  let probing = $state({})      // server_id → true while a live Test runs
  let probeRes = $state({})     // server_id → {ok,msg}
  let usage = $state([])
  let usageErr = $state('')
  let toolsOpen = $state(null)  // server_id with the tools browser expanded
  let toolsState = $state({})   // server_id → {loading|tools|error}
  let drift = $state(null)
  let resyncMsg = $state(null)

  async function loadHealth() {
    try {
      const h = await api.mcpHealth()
      const map = {}
      for (const s of (h.servers ?? [])) map[s.server_id] = s
      healthMap = map
    } catch (_) { healthMap = {} }
  }
  async function loadUsage() {
    usageErr = ''
    try { const u = await api.mcpUsage(30); usage = u.rows ?? []; if (u.error) usageErr = 'Usage query failed (check UI logs).' }
    catch (e) { usageErr = e.message }
  }
  async function loadDrift() { try { drift = await api.drift() } catch (_) { drift = null } }
  onMount(() => { loadHealth(); loadUsage(); loadDrift() })

  let mcpDrift = $derived(drift?.mcp && !drift.mcp.error ? drift.mcp : null)
  let mcpDriftCount = $derived(mcpDrift
    ? (mcpDrift.missing_in_litellm?.length || 0) + (mcpDrift.extra_in_litellm?.length || 0) + (mcpDrift.content_drifted?.length || 0)
    : 0)

  // refresh live views after a successful Apply (same pattern as Models.svelte)
  let _prevApplying = false
  $effect(() => {
    const cur = store.applying
    if (_prevApplying && !cur && !store.error) { loadHealth(); loadUsage(); loadDrift() }
    _prevApplying = cur
  })

  async function resyncToProxy() {
    resyncMsg = null
    try {
      const r = await api.resync()
      const m = r.mcp || {}
      resyncMsg = { ok: true, text: `Resynced — MCP: ${m.added || 0} added, ${m.updated || 0} updated, ${m.deleted || 0} deleted${m.failed?.length ? `, ${m.failed.length} failed` : ''}.` }
    } catch (e) { resyncMsg = { ok: false, text: e.message } }
    await loadDrift(); await loadHealth()
  }

  function resetForm() {
    form = { server_name: '', description: '', transport: 'http', url: '', auth_type: '',
             auth_value: '', allow_all_keys: false, default_cost: '', hasStoredSecret: false }
    headerRows = []; extraRows = []; toolRows = []; costRows = []
    editingId = null; showAdd = false; formErr = ''
  }

  function editServer(item) {
    const d = item.data || {}
    const ci = d.mcp_info?.mcp_server_cost_info || {}
    form = { server_name: d.server_name || '', description: d.description || '',
             transport: d.transport || 'http', url: d.url || '',
             auth_type: d.auth_type || '', auth_value: '',
             allow_all_keys: !!d.allow_all_keys,
             default_cost: ci.default_cost_per_query ?? '',
             hasStoredSecret: !!d.auth_value_encrypted }
    headerRows = dictToHeaderRows(d.static_headers)
    extraRows = arrayToListRows(d.extra_headers)
    toolRows = arrayToListRows(d.allowed_tools)
    costRows = dictToCostRows(ci.tool_name_to_cost_per_query)
    editingId = item.name; showAdd = true; formErr = ''
  }

  async function saveServer() {
    formErr = ''
    const v = validateMcpForm(form)
    if (v) { formErr = v; return }
    const id = editingId || uuidv4()
    const ok = await store.stageItem('mcp_server', id, {
      server_name: form.server_name.trim(),
      description: form.description.trim(),
      transport: form.transport,
      url: form.url.trim(),
      auth_type: form.auth_type || null,
      auth_value: form.auth_value,          // blank on edit = keep stored secret (server-side)
      static_headers: headerRowsToDict(headerRows),
      extra_headers: listRowsToArray(extraRows),
      allowed_tools: listRowsToArray(toolRows),
      allow_all_keys: form.allow_all_keys,
      mcp_info: buildMcpInfo(form.default_cost, costRows),
    })
    if (ok) resetForm()   // keep input on a rejected save (422)
  }

  async function probeServer(item) {
    probing = { ...probing, [item.name]: true }
    try {
      const h = await api.mcpHealth(1, item.name)
      const err = h.probe_error
      probeRes = { ...probeRes, [item.name]: err ? { ok: false, msg: err } : { ok: true, msg: 'Reachable' } }
      await loadHealth()
    } catch (e) {
      probeRes = { ...probeRes, [item.name]: { ok: false, msg: e.message } }
    } finally {
      probing = { ...probing, [item.name]: false }
    }
  }

  async function toggleTools(item) {
    if (toolsOpen === item.name) { toolsOpen = null; return }
    toolsOpen = item.name
    if (!toolsState[item.name]) {
      toolsState = { ...toolsState, [item.name]: { loading: true } }
      try {
        const r = await api.mcpTools(item.name)
        const tools = Array.isArray(r) ? r : (r.tools ?? r.data ?? [])
        toolsState = { ...toolsState, [item.name]: { tools } }
      } catch (e) {
        toolsState = { ...toolsState, [item.name]: { error: e.message } }
      }
    }
  }

  function addHeader() { headerRows = [...headerRows, { k: '', v: '' }] }
  function rmHeader(i) { headerRows = headerRows.filter((_, j) => j !== i) }
  function addExtra() { extraRows = [...extraRows, ''] }
  function rmExtra(i) { extraRows = extraRows.filter((_, j) => j !== i) }
  function addTool() { toolRows = [...toolRows, ''] }
  function rmTool(i) { toolRows = toolRows.filter((_, j) => j !== i) }
  function addCost() { costRows = [...costRows, { tool: '', cost: '' }] }
  function rmCost(i) { costRows = costRows.filter((_, j) => j !== i) }

  function healthInfo(item) {
    if (item.flag === 'new') return { color: '#c7c7cc', title: 'Not applied yet' }
    const h = healthMap[item.name]
    if (!h || !h.status || h.status === 'unknown') return { color: '#8e8e93', title: 'Health unknown — use Test' }
    if (h.status === 'healthy') return { color: '#34c759', title: `Healthy (checked ${h.last_health_check ? fmtDateTime(h.last_health_check) : '—'})` }
    return { color: '#ff3b30', title: h.health_check_error || h.status }
  }

  function flagAccent(flag) {
    if (flag === 'new') return 'row-new'
    if (flag === 'changed') return 'row-changed'
    if (flag === 'deleted') return 'row-deleted'
    return ''
  }
</script>

<div class="page">
  <header><h1>MCP Servers</h1>
    {#if mcpDrift}
      <span class="drift" class:ok={mcpDriftCount === 0} class:warn={mcpDriftCount > 0}
        title={mcpDriftCount === 0 ? 'ui_config and the proxy agree' : 'ui_config and the proxy differ'}>
        {mcpDriftCount === 0 ? 'In sync ✓' : `⚠ ${mcpDriftCount} out of sync`}
      </span>
      {#if mcpDriftCount > 0}
        <button onclick={resyncToProxy} disabled={store.applying || store.saving}>Resync to proxy</button>
      {/if}
    {/if}
    <button class="primary" onclick={() => { editingId = null; showAdd = !showAdd; formErr = '' }} disabled={store.applying}>＋ Add MCP server</button>
  </header>
  {#if resyncMsg}<div class="banner {resyncMsg.ok ? 'ok' : 'err'}">{resyncMsg.text}</div>{/if}
  {#if store.error}<div class="banner err">{store.error}</div>{/if}
  {#if store.notice}<div class="banner ok">{store.notice}</div>{/if}

  {#if showAdd}
    <div class="card add">
      <h3 style="margin:0 0 4px">{editingId ? 'Edit MCP server' : 'Add MCP server'}</h3>
      <label>Server name <input bind:value={form.server_name} placeholder="e.g. firecrawl" />
        <span class="hint">Letters, digits, _ or -. Becomes the tool prefix (<code>firecrawl-scrape</code>) and the per-server endpoint (<code>/firecrawl/mcp</code>).</span>
      </label>
      <label>Description <input bind:value={form.description} placeholder="optional" /></label>
      <label>Transport
        <select bind:value={form.transport}>
          <option value="http">Streamable HTTP</option>
          <option value="sse">SSE</option>
        </select>
      </label>
      <label>URL <input bind:value={form.url} placeholder="http://10.0.20.x:3002/mcp" />
        <span class="hint">⚠ Stored and displayed in plain text — if a vendor embeds the API key in the URL, prefer header auth or a self-hosted instance.</span>
      </label>
      <label>Auth
        <select bind:value={form.auth_type}>
          <option value="">None</option>
          <option value="api_key">API key</option>
          <option value="bearer_token">Bearer token</option>
          <option value="basic">Basic</option>
        </select>
      </label>
      {#if form.auth_type}
        <label>Auth value
          <input type="password" bind:value={form.auth_value}
                 placeholder={form.hasStoredSecret ? '(unchanged — leave blank to keep)' : 'secret'} />
          <span class="hint">Encrypted at rest; never shown again. Blank on edit keeps the stored secret.</span>
        </label>
      {/if}
      <div class="rows">
        <span class="field-name">Static headers <span class="hint">(sent on every request — no secrets here, use Auth)</span></span>
        {#each headerRows as row, i}
          <div class="kv-row">
            <input placeholder="Header" bind:value={row.k} />
            <input placeholder="value" bind:value={row.v} />
            <button type="button" class="x" onclick={() => rmHeader(i)} aria-label="remove header">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addHeader}>+ Add header</button>
      </div>
      <div class="rows">
        <span class="field-name">Forwarded client headers</span>
        {#each extraRows as _, i}
          <div class="kv-row">
            <input placeholder="Header name (e.g. Authorization)" bind:value={extraRows[i]} />
            <button type="button" class="x" onclick={() => rmExtra(i)} aria-label="remove">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addExtra}>+ Add forwarded header</button>
      </div>
      <div class="rows">
        <span class="field-name">Allowed tools <span class="hint">(blank = all tools exposed)</span></span>
        {#each toolRows as _, i}
          <div class="kv-row">
            <input placeholder="tool name" bind:value={toolRows[i]} />
            <button type="button" class="x" onclick={() => rmTool(i)} aria-label="remove">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addTool}>+ Add tool</button>
      </div>
      <label class="check"><input type="checkbox" bind:checked={form.allow_all_keys} />
        Allow all virtual keys
        <span class="hint">Every key may use this server without an explicit grant on the Keys page.</span>
      </label>
      <label>Default cost per tool call ($) <input type="number" min="0" step="0.001" bind:value={form.default_cost} placeholder="0 = free" /></label>
      <div class="rows">
        <span class="field-name">Per-tool cost overrides</span>
        {#each costRows as row, i}
          <div class="kv-row">
            <input placeholder="tool name" bind:value={row.tool} />
            <input type="number" min="0" step="0.001" placeholder="$ per call" bind:value={row.cost} />
            <button type="button" class="x" onclick={() => rmCost(i)} aria-label="remove">✕</button>
          </div>
        {/each}
        <button type="button" class="addrow" onclick={addCost}>+ Add tool cost</button>
      </div>
      {#if formErr}<div class="banner err">{formErr}</div>{/if}
      <div class="row">
        <button class="primary" onclick={saveServer} disabled={store.saving || store.applying}>Save</button>
        <button onclick={resetForm}>Cancel</button>
      </div>
      <p class="hint">Saved changes are staged — click <strong>Apply</strong> to push them to the gateway (hot, no proxy restart).</p>
    </div>
  {/if}

  <div class="card">
    {#if mcpItems.length === 0}<p class="empty">No MCP servers yet. Add one to unify your MCP tools behind the proxy.</p>
    {:else}
      <table>
        <thead><tr><th>Name</th><th>Transport</th><th>URL</th><th>Auth</th><th>Keys</th><th>Health</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {#each mcpItems as item (item.name)}
            {@const d = item.data || {}}
            {@const flag = item.flag}
            {@const dot = healthInfo(item)}
            {@const pr = probeRes[item.name]}
            <tr class={flagAccent(flag)}>
              <td class:strikethrough={flag === 'deleted'}><strong>{d.server_name}</strong>
                {#if d.description}<div class="hint">{d.description}</div>{/if}</td>
              <td class:strikethrough={flag === 'deleted'}>{d.transport}</td>
              <td class:strikethrough={flag === 'deleted'} class="trunc" title={d.url}><code>{d.url}</code></td>
              <td>{d.auth_type || '—'}</td>
              <td>{d.allow_all_keys ? 'all' : 'granted'}</td>
              <td><span class="dot" style="background:{dot.color}" title={dot.title}></span>
                {#if flag !== 'deleted'}
                  <button class="small" onclick={() => probeServer(item)} disabled={probing[item.name]}>{probing[item.name] ? '…' : 'Test'}</button>
                  {#if pr}<span class="check-res" class:ok={pr.ok} class:bad={!pr.ok} title={pr.msg}>{pr.ok ? '✓' : '✗'}</span>{/if}
                {/if}
              </td>
              <td>
                {#if flag === 'new'}<span class="flag-tag flag-new">new</span>
                {:else if flag === 'changed'}<span class="flag-tag flag-changed">changed</span>
                {:else if flag === 'deleted'}<span class="flag-tag flag-deleted">deleted</span>{/if}
              </td>
              <td class="actions">
                {#if flag === 'deleted'}
                  <button class="undo" onclick={() => store.discard('mcp_server', item.name)} disabled={store.saving || store.applying}>Undo</button>
                {:else}
                  <button class="small" onclick={() => toggleTools(item)} disabled={flag === 'new'}
                          title={flag === 'new' ? 'Apply first — tools come from the live gateway' : 'List tools this server exposes'}>
                    {toolsOpen === item.name ? 'Hide tools' : 'Tools'}
                  </button>
                  <button onclick={() => editServer(item)} disabled={store.saving || store.applying}>Edit</button>
                  <button class="danger" onclick={() => store.deleteItem('mcp_server', item.name)} disabled={store.saving || store.applying}>Delete</button>
                {/if}
              </td>
            </tr>
            {#if toolsOpen === item.name}
              {@const ts = toolsState[item.name]}
              <tr class="detail-row"><td colspan="8">
                {#if ts?.loading}<p class="empty">Loading tools…</p>
                {:else if ts?.error}<p class="empty">Couldn't list tools — {ts.error}</p>
                {:else if ts?.tools}
                  {#if ts.tools.length === 0}<p class="empty">No tools exposed.</p>
                  {:else}
                    <ul class="tool-list">
                      {#each ts.tools as t}<li><code>{t.name}</code>{#if t.description} — <span class="hint">{t.description}</span>{/if}</li>{/each}
                    </ul>
                  {/if}
                {/if}
              </td></tr>
            {/if}
          {/each}
        </tbody>
      </table>
    {/if}
  </div>

  <div class="card">
    <h3 style="margin:0 0 8px">Usage (last 30 days)</h3>
    {#if usageErr}<div class="banner err">{usageErr}</div>{/if}
    {#if usage.length === 0}<p class="empty">No MCP tool calls recorded yet.</p>
    {:else}
      <table>
        <thead><tr><th>Server</th><th>Calls</th><th>Failures</th><th>Spend</th><th>Last call</th></tr></thead>
        <tbody>
          {#each usage as u}
            <tr>
              <td>{u.server}</td>
              <td>{u.calls.toLocaleString()}</td>
              <td class:red={u.failures > 0}>{u.failures}</td>
              <td>{money(u.spend)}</td>
              <td class="nowrap">{fmtDateTime(u.last_call)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>

  <div class="card">
    <h3 style="margin:0 0 8px">Connecting clients</h3>
    <p class="hint" style="font-size:13px">
      Point MCP clients at <code>http://&lt;proxy-host&gt;:8000/mcp</code> (streamable HTTP) with header
      <code>x-litellm-api-key: &lt;virtual key&gt;</code>. Scope to specific servers with
      <code>x-mcp-servers: name1,name2</code>, or use a per-server endpoint
      <code>/&lt;server_name&gt;/mcp</code>. Tools are namespaced <code>&lt;server_name&gt;-&lt;tool&gt;</code>.
      Grant keys access on the <strong>Virtual Keys</strong> page.
    </p>
  </div>
</div>

<style>
  .page{padding:24px 30px;max-width:1000px}
  header{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
  .card{border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px;margin-top:14px;background:#fff}
  .card.add{display:flex;flex-direction:column;gap:10px;max-width:560px}
  label{display:flex;flex-direction:column;font-size:13px;color:#3a3a3c;gap:4px}
  input,select{padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px;border-bottom:1px solid rgba(0,0,0,.06);font-size:14px}
  .row{display:flex;gap:8px;margin-top:4px}
  button{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:#0a84ff;color:#fff;border:0}
  button.danger{color:#ff3b30;border-color:#ffd0cc}
  button.undo{color:#ff9500;border-color:#ffe0b2}
  button.small{padding:4px 10px;font-size:12px}
  button:disabled{opacity:.5;cursor:default}
  .banner{padding:10px 12px;border-radius:8px;margin-top:8px;font-size:13px}
  .banner.err{background:#ffeceb;color:#c0271d}.banner.ok{background:#e7f7ec;color:#1d7a33}
  .hint{font-size:11px;color:#6e6e73}
  .empty{color:#6e6e73}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .check-res{margin-left:6px;font-weight:600}
  .check-res.ok{color:#34c759}.check-res.bad{color:#ff3b30}
  .actions{display:flex;gap:6px;flex-wrap:wrap}
  .rows{display:flex;flex-direction:column;gap:4px}
  .field-name{font-size:13px;color:#3a3a3c}
  .kv-row{display:flex;gap:6px;align-items:center}
  .kv-row input{flex:1;min-width:0}
  .x{border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer;padding:4px 9px}
  .addrow{margin-top:2px;font-size:12px;padding:3px 12px;border:1px solid rgba(0,0,0,.15);border-radius:7px;background:#fff;cursor:pointer;width:fit-content}
  label.check{flex-direction:row;align-items:flex-start;gap:8px;flex-wrap:wrap}
  label.check input{margin-top:2px}
  .drift{font-size:12px;padding:3px 10px;border-radius:20px}
  .drift.ok{background:#e7f7ec;color:#1d7a33}
  .drift.warn{background:#fff4e5;color:#9a5b00}
  .row-new{background:rgba(10,132,255,.06)}
  .row-changed{background:rgba(255,149,0,.06)}
  .row-deleted{background:rgba(255,59,48,.05)}
  .strikethrough{text-decoration:line-through;color:#8e8e93}
  .flag-tag{display:inline-block;font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;text-transform:uppercase;letter-spacing:.04em}
  .flag-new{background:rgba(10,132,255,.12);color:#0a52c7}
  .flag-changed{background:rgba(255,149,0,.15);color:#b36800}
  .flag-deleted{background:rgba(255,59,48,.12);color:#c0271d}
  .detail-row td{background:#fafafc;white-space:normal}
  .tool-list{margin:6px 0;padding-left:18px;font-size:13px}
  .trunc{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .nowrap{white-space:nowrap}
  .red{color:#c0271d}
</style>
```

- [ ] **Step 3: Build + smoke** — `npm run build` clean. Then serve the dev stack UI (bind LAN IP, never localhost: `npm run dev -- --host 0.0.0.0`) and verify by loading `http://10.0.20.85:<port>`: page renders, Add form validates (bad name → inline error), staging a server shows the flag pill + global Apply bar.
- [ ] **Step 4: Commit** — `feat(ui): MCP Servers page (staged CRUD, health, tools, usage)` + trailer.

---

### Task 11: Keys page MCP grants picker

**Files:**
- Modify: `ui/frontend/src/routes/Keys.svelte`

**Interfaces:**
- Consumes: `api.configState()` (already fetched in `load()`), key objects from `/api/keys` (`k.object_permission?.mcp_servers` — Task 1 Step 6 recorded whether `/key/list?return_full_object=true` includes it).
- Produces: `object_permission: {mcp_servers: [...]}` included in create/update payloads **only when the selection changed**; clearing sends `[]`.

**Fallback (ONLY if Task 1 Step 6 recorded that `/key/list` omits `object_permission`):** add to `ui/app/keys_client.py`:

```python
    async def key_info(self, token: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"{self._base}/key/info", params={"key": token})
            r.raise_for_status()
            d = r.json()
            return d.get("info", d)
```

a route in `ui/app/routes/keys_routes.py`:

```python
@router.get("/keys/info", dependencies=[Depends(login_required)])
async def key_info(token: str):
    try:
        return await make_keys_client().key_info(token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"proxy key API error: {e}")
```

and in `editKey(k)` populate the grants from `await api.get('/api/keys/info?token=' + encodeURIComponent(k.token))` (make `editKey` async; default to `[]` on fetch error) instead of `k.object_permission`. Add a MockTransport-style test mirroring `test_keys_routes.py` patterns for the new route.

- [ ] **Step 1: Script changes in `Keys.svelte`:**

After the `passthroughRows` block (line ~43) add:

```js
  let mcpOptions = $state([])        // [{id, name}] from applied+staged mcp_server items
  let mcpGrants = $state([])         // selected server ids
  let mcpInitial = $state('[]')      // JSON of the sorted initial selection (change detection)
  function toggleGrant(id) {
    mcpGrants = mcpGrants.includes(id) ? mcpGrants.filter(g => g !== id) : [...mcpGrants, id]
  }
```

In `load()`, extend the configState usage (same `state` object already fetched):

```js
      mcpOptions = (state.items || [])
        .filter(i => i.kind === 'mcp_server' && i.flag !== 'deleted')
        .map(i => ({ id: i.name, name: i.data?.server_name || i.name, allowAll: !!i.data?.allow_all_keys }))
        .sort((a, b) => a.name.localeCompare(b.name))
```

In `resetFb()` append: `mcpGrants = []; mcpInitial = '[]'`.

In `editKey(k)` (near the `passthroughRows` line) add:

```js
    mcpGrants = [...((k.object_permission?.mcp_servers) || [])]
    mcpInitial = JSON.stringify([...mcpGrants].sort())
```

In `buildKeyFields()` before the final `Object.keys(payload)` cleanup:

```js
    // MCP grants: send object_permission only when the selection changed, so
    // unrelated key edits never churn the permission row. Empty list = revoke all.
    const grants = [...mcpGrants].sort()
    if (JSON.stringify(grants) !== mcpInitial) payload.object_permission = { mcp_servers: grants }
```

- [ ] **Step 2: Markup** — after the passthrough `<div class="passthrough">…</div>` block add:

```svelte
      {#if mcpOptions.length}
        <div class="passthrough">
          <span class="field-name">MCP servers</span>
          {#each mcpOptions as s}
            <label class="mcp-opt">
              <input type="checkbox" checked={mcpGrants.includes(s.id)} onchange={() => toggleGrant(s.id)}
                     disabled={s.allowAll} />
              {s.name}{#if s.allowAll}<span class="hint"> (all keys allowed)</span>{/if}
            </label>
          {/each}
          <span class="hint">MCP servers this key may reach through the gateway (<code>/mcp</code>). Nothing selected = no MCP access, except servers marked "all keys".</span>
        </div>
      {/if}
```

and to the `<style>` block:

```css
  .mcp-opt{flex-direction:row;align-items:center;gap:8px;font-size:13px;margin:2px 0}
```

- [ ] **Step 3: Build + manual verify** — `npm run build`; on the dev stack: edit a key → tick a server → Save → re-open → tick persists (proves `object_permission` round-trips). Save an unrelated edit → confirm the request payload has NO `object_permission` (devtools network tab).
- [ ] **Step 4: Commit** — `feat(ui): per-key MCP server grants picker` + trailer.

---

### Task 12: Activity feed MCP visibility

**Files:**
- Modify: `ui/app/routes/usage_routes.py`, `ui/frontend/src/routes/ActivityFeed.svelte`
- Test: extend `ui/tests/test_usage_activity.py`

**Interfaces:**
- Consumes: SpendLogs metadata paths proven in Task 1 Step 7.
- Produces: activity rows gain `mcp_server`, `mcp_tool` (strings, `''` when not MCP); `/api/usage/activity` accepts `type=all|llm|mcp`; `/api/usage/tx/{id}` gains `mcp: {server, tool, arguments, result} | null`.

- [ ] **Step 1: Write failing tests** — append to `ui/tests/test_usage_activity.py` (pure-helper tests, matching that file's existing style):

```python
def test_activity_where_type_filters():
    from app.routes.usage_routes import _activity_where
    sql_mcp, _ = _activity_where(7, type_="mcp")
    assert "l.call_type IN ('call_mcp_tool','list_mcp_tools')" in sql_mcp
    sql_llm, _ = _activity_where(7, type_="llm")
    assert "l.call_type IS NULL OR l.call_type NOT IN" in sql_llm
    sql_all, _ = _activity_where(7)
    assert "call_mcp_tool" not in sql_all


def test_shape_activity_row_mcp_fields():
    from app.routes.usage_routes import _shape_activity_row
    r = _shape_activity_row({"id": "r1", "time": None, "model": "", "provider": "", "key": "k",
                             "tok_in": 0, "tok_out": 0, "spend": 0, "latency_ms": 1,
                             "status": "success", "cache_hit": None, "call_type": "call_mcp_tool",
                             "mcp_server": "deepwiki", "mcp_tool": "read_wiki_structure"})
    assert r["mcp_server"] == "deepwiki" and r["mcp_tool"] == "read_wiki_structure"


def test_extract_mcp_from_metadata():
    import json
    from app.routes.usage_routes import _extract_mcp
    meta = json.dumps({"mcp_tool_call_metadata": {
        "mcp_server_name": "deepwiki", "name": "read_wiki_structure",
        "arguments": {"repoName": "x"}, "result": {"ok": True}}})
    m = _extract_mcp(meta)
    assert m == {"server": "deepwiki", "tool": "read_wiki_structure",
                 "arguments": {"repoName": "x"}, "result": {"ok": True}}
    assert _extract_mcp(None) is None
    assert _extract_mcp("not json") is None
    assert _extract_mcp(json.dumps({})) is None
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Backend implementation in `usage_routes.py`:**

`_ACTIVITY_SELECT` — add the MCP metadata extractions (after `l.call_type`):

```python
_ACTIVITY_SELECT = (
    'SELECT l.request_id id, l."startTime" time, l.model, l.custom_llm_provider provider, '
    'COALESCE(v.key_alias, LEFT(l.api_key,10)) key, l.prompt_tokens tok_in, '
    'l.completion_tokens tok_out, l.spend, l.request_duration_ms latency_ms, '
    'l.status, l.cache_hit, l.call_type, '
    "l.metadata::jsonb #>> '{mcp_tool_call_metadata,mcp_server_name}' mcp_server, "
    "l.metadata::jsonb #>> '{mcp_tool_call_metadata,name}' mcp_tool "
    'FROM "LiteLLM_SpendLogs" l LEFT JOIN "LiteLLM_VerificationToken" v ON v.token=l.api_key ')
```

`_activity_where` — add keyword param and clauses:

```python
_MCP_CALL_TYPES_SQL = "('call_mcp_tool','list_mcp_tools')"

def _activity_where(days, status="all", model=None, key=None, cursor=None, type_="all"):
    ...
    if type_ == "mcp":
        clauses.append(f"l.call_type IN {_MCP_CALL_TYPES_SQL}")
    elif type_ == "llm":
        clauses.append(f"(l.call_type IS NULL OR l.call_type NOT IN {_MCP_CALL_TYPES_SQL})")
    if cursor:
        ...
```

(insert the type clauses BEFORE the cursor clause so param numbering is unchanged — the type clauses carry no params).

`_shape_activity_row` — add:

```python
            "cache_hit": _cache_bool(r["cache_hit"]), "call_type": r.get("call_type") or "",
            "mcp_server": r.get("mcp_server") or "", "mcp_tool": r.get("mcp_tool") or ""}
```

`usage_activity` route — accept and validate the param, pass it through:

```python
from fastapi import Query   # extend existing fastapi import

async def usage_activity(days: int = 30, status: str = "all", model: str = "",
                         key: str = "", cursor: str = "", limit: int = 50, stats: int = 0,
                         type_: str = Query("all", alias="type")):
    ...
    if type_ not in ("all", "llm", "mcp"):
        raise HTTPException(status_code=422, detail="type must be all|llm|mcp")
    ...
    where, params = _activity_where(days, status, model or None, key or None, cur, type_)
```

New helper `_extract_mcp` (next to `_extract_error`):

```python
def _extract_mcp(metadata):
    """metadata.mcp_tool_call_metadata → {server, tool, arguments, result} | None.
    Defensive: jsonb arrives as str from asyncpg; malformed shapes → None."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            return None
    if not isinstance(metadata, dict):
        return None
    m = metadata.get("mcp_tool_call_metadata")
    if not isinstance(m, dict):
        return None
    return {"server": m.get("mcp_server_name") or "", "tool": m.get("name") or "",
            "arguments": m.get("arguments"), "result": m.get("result")}
```

`_shape_tx` — add to the returned dict (metadata is already selected):

```python
            "mcp": _extract_mcp(r.get("metadata")),
            "error": _extract_error(r.get("metadata")) if r["status"] == "failure" else None}
```

- [ ] **Step 4: Frontend `ActivityFeed.svelte`:**

Add filter state next to `fKey`: `let fType = $state('all')`; include in both query builders (`loadFirst`/`loadMore` param objects): `type: fType` (history mode object in `loadFirst`; always in `loadMore`); add `fType` to the reload `$effect` dependency list (`mode; days; fStatus; fModel; fKey; fType;`).

History chips — after the status `seg`:

```svelte
      <div class="seg small">
        {#each [['all','All types'],['llm','LLM'],['mcp','MCP']] as [v, label]}
          <button class="seg-btn" class:active={fType === v} onclick={() => fType = v}>{label}</button>
        {/each}
      </div>
```

Row Model cell (replace the current `<td class="trunc" ...>`):

```svelte
              <td class="trunc" title={r.mcp_server ? `${r.mcp_server} · ${r.mcp_tool}` : r.model}>
                {#if r.call_type === 'call_mcp_tool' || r.call_type === 'list_mcp_tools'}
                  <span class="mcp-tag">MCP</span> {r.mcp_server || '?'}{r.mcp_tool ? ` · ${r.mcp_tool}` : ''}
                {:else}{r.model || '—'}{/if}
              </td>
```

Detail pane — inside the `dgrid`, after the Route row:

```svelte
                    {#if t.mcp}
                      <span class="dl">MCP</span>
                      <span class="dv">{t.mcp.server || '—'}{t.mcp.tool ? ` · ${t.mcp.tool}` : ''}</span>
                    {/if}
```

and after the `dgrid` div, before the error box:

```svelte
                  {#if t.mcp && (t.mcp.arguments || t.mcp.result)}
                    <div class="mcpbox">
                      {#if t.mcp.arguments}<details open><summary>Arguments</summary><pre>{JSON.stringify(t.mcp.arguments, null, 2)}</pre></details>{/if}
                      {#if t.mcp.result}<details><summary>Result</summary><pre>{JSON.stringify(t.mcp.result, null, 2)}</pre></details>{/if}
                    </div>
                  {/if}
```

Styles — add:

```css
  .mcp-tag{display:inline-block;font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;background:rgba(94,92,230,.15);color:#3634a3;text-transform:uppercase;letter-spacing:.04em}
  .mcpbox{margin:8px 2px 4px;padding:8px 12px;background:#f4f4f8;border-radius:8px;font-size:13px}
  .mcpbox pre{margin:6px 0 0;max-height:240px;overflow:auto;font-size:11px;white-space:pre-wrap}
  .mcpbox summary{cursor:pointer;font-size:12px;color:#6e6e73}
```

- [ ] **Step 5: Run** — pytest suite green; `npm run build` clean.
- [ ] **Step 6: Commit** — `feat(ui): MCP tool calls in the Activity feed (rows, filter, detail)` + trailer.

---

### Task 13: Local e2e, docs, final green run

**Files:**
- Create: `docs/mcp-gateway.md`
- Modify: none beyond fixes the e2e surfaces

**Interfaces:** consumes everything above. Produces the branch in mergeable state.

- [ ] **Step 1: Rebuild the local UI container against this branch and bring the stack up**

```bash
cd /home/kumar/workspace/litellm
docker compose build llm-proxy-ui && docker compose up -d
```
(If the compose file pins `ghcr.io/...` for the UI with no build stanza, run the backend directly instead: `cd ui && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080` with env from `.env`, after `cd ui/frontend && npm run build` — the SPA is served from the build dir. Verify via the LAN IP, not localhost.)

- [ ] **Step 2: Full loop against deepwiki** (admin password: local stack `Smoke-Admin-2026`):
  1. MCP Servers page → Add: name `deepwiki`, transport HTTP, url `https://mcp.deepwiki.com/mcp`, no auth → Save → flag pill `new` → Apply. Expected notice: `Applied live — … ; MCP — 1 added, 0 updated, 0 deleted`. Record litellm container `StartedAt` before/after (`docker inspect --format '{{.State.StartedAt}}' $(docker compose ps -q litellm)`) — MUST be unchanged (hot apply).
  2. Health: badge turns green after Test; Tools button lists deepwiki tools.
  3. Keys page → create key `mcp-e2e` with the deepwiki grant ticked → copy key.
  4. Tool call through the gateway with that key (curl `POST /mcp-rest/tools/call` as in Task 1 Step 7).
  5. Usage & Spend → Activity: the call appears with the `MCP` badge (`deepwiki · read_wiki_structure`); History → type filter MCP shows it; row detail shows Arguments/Result. MCP page Usage card shows 1 call.
  6. Drift: badge `In sync ✓`. Then delete the server directly in LiteLLM (`curl -X DELETE $BASE/v1/mcp/server/<uuid> -H master`) → reload page → drift warns → Resync → in sync again.
  7. Revoke: untick the grant on the key → Save → tool call now denied.
  8. Edit-with-secret check: edit server, set auth bearer `dummy`, Save+Apply, re-edit — placeholder shows `(unchanged — leave blank to keep)`; leave blank, change description, Save+Apply → apply succeeds (blank-means-keep).
  9. Cleanup: delete the e2e key; keep or delete the deepwiki server at your discretion.
  Record any false content-drift here and fix per Task 6's caveat before proceeding.

- [ ] **Step 3: Write `docs/mcp-gateway.md`** — admin guide (add/edit servers, hot apply, grants, drift/resync, health/usage) + client onboarding (endpoints `/mcp`, `/{server_name}/mcp`, `/sse`; headers `x-litellm-api-key`, `x-mcp-servers`; tool namespacing; the spec §11 caveats: URL-embedded keys, static headers plaintext, auth-removal = delete + re-add). Cross-link from `docs/config-schema.md` with a one-paragraph `mcp_server` kind description (fields table from the spec §4).

- [ ] **Step 4: Full verification**

```bash
cd /home/kumar/workspace/litellm/ui && .venv/bin/python -m pytest tests/ -q
cd /home/kumar/workspace/litellm/ui/frontend && npm run build
```
Expected: suite green (309+new passed, 1 skipped), build clean.

- [ ] **Step 5: Commit** — `docs: MCP gateway admin + client guide` + trailer. Update `.superpowers/sdd/progress.md` ledger.

---

## After the plan

Whole-branch final review (superpowers:requesting-code-review), then superpowers:finishing-a-development-branch: merge --no-ff to main → semantic-release cuts **1.33.0** → pin bump → `.75` UI-only deploy (`docker compose up -d --no-deps llm-proxy-ui`; litellm untouched) → onboard firecrawl (hosted + self-hosted), searxng, hindsight through the UI on `.75` (at least one with real auth) → memory topic update. These are controller steps, not plan tasks.
