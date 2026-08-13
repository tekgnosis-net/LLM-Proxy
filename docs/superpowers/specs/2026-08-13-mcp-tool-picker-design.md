# MCP Allowed-Tools Fetched Picker (v3.28) — Design

**Date:** 2026-08-13
**Status:** Approved design (user LGTM 2026-08-13; spec-review gate waived — "go ahead with spec & implementation")
**Branch:** `v3.28-mcp-tool-picker`
**Depends on:** MCP gateway v3.27 (shipped, 1.33.0)
**Follow-on (separate cycle):** v3.29 per-key tool ACLs (`object_permission.mcp_tool_permissions`) — needs its own live OSS proof gate first; NOT in this spec.

## 1. Goal

The MCP Servers add/edit form's "Allowed tools" list is free text — the admin must remember
tool names. Add a **Fetch tools** button that discovers the server's actual tools and turns
the list into a checkbox picker. Crucially it must work **before the server is saved/applied**
(LiteLLM can only list tools for registered servers), so the UI backend probes the MCP server
directly using the connection details currently entered in the form.

## 2. Verified facts (live, 2026-08-13)

1. **Direct probe works with plain httpx** (no `mcp` SDK dependency) against a real streamable-HTTP
   MCP server (deepwiki 2.14.3): `POST initialize` (with `Accept: application/json, text/event-stream`)
   → `POST notifications/initialized` (202) → `POST tools/list` → tools with names + descriptions.
   Responses may be **SSE-framed** (`content-type: text/event-stream`, payload on a `data:` line)
   or plain JSON — both shapes must be parsed. `mcp-session-id` response header is **optional**
   (deepwiki issues none); when present it must be echoed on subsequent requests.
2. **Auth header mapping** (from LiteLLM's own client, `experimental_mcp_client/client.py:351-376` —
   the probe must send exactly what the gateway would):
   - `bearer_token` → `Authorization: Bearer <value>`
   - `basic` → `Authorization: Basic <value>` (value used **verbatim**, LiteLLM does not base64)
   - `api_key` → `X-API-Key: <value>`
3. The v3.27 blank-means-keep convention: an edit form with a stored secret sends a blank
   `auth_value`; the ciphertext lives in the item's `auth_value_encrypted` (Fernet vault).

## 3. Scope

**In:** direct tool preview for `transport: http`; picker UI on the MCP Servers form; stored-secret
reuse for edits; `static_headers` included in the probe.

**Out (unchanged/deferred):** SSE-transport direct preview (friendly 422; applied SSE servers
already have the LiteLLM-backed Tools browser); per-key tool ACLs (v3.29); any change to
`allowed_tools` semantics (empty = all tools; enforcement stays LiteLLM-side); apply pipeline,
schema, reconciler — all untouched.

## 4. Backend

### 4a. New module `ui/app/mcp_probe.py` (pure-ish, httpx-injectable)

```python
def build_probe_headers(auth_type, auth_value, static_headers) -> dict
    # exact LiteLLM mapping from §2.2 + static_headers overlaid + the JSON-RPC
    # Accept/Content-Type headers
def parse_rpc_response(response) -> dict | None
    # SSE-framed (first `data:` line) or plain JSON body → parsed JSON-RPC message
async def probe_tools(url, auth_type, auth_value, static_headers,
                      transport="http", timeout=10.0, http_transport=None) -> list[dict]
    # initialize → (echo optional mcp-session-id) → notifications/initialized →
    # tools/list → [{"name", "description"}]; raises ProbeError(status_hint, message)
    # on non-http transport, HTTP errors, malformed frames, or JSON-RPC error objects
```

`ProbeError` carries a short human message ("server returned 401 — check the auth value",
"SSE transport can't be previewed directly — Apply the server first and use Tools, or type names").

### 4b. New endpoint in `ui/app/routes/mcp_routes.py`

`POST /api/mcp/tools/preview` (login_required). Body:
`{url, transport, auth_type, auth_value, static_headers, server_id}`.

- Validates url (http/https) and transport (`http` only → 422 with the friendly SSE message
  otherwise).
- **Stored-secret path:** `auth_type` set + blank `auth_value` + `server_id` present → look up
  the effective `mcp_server` item by name (`server_id` is the item uuid) via the config store
  and Fernet-decrypt its `auth_value_encrypted` (404-style 422 if none — same rule as staging).
- Calls `probe_tools`; returns `{"tools": [{"name","description"}]}`; `ProbeError` → 422 with
  the message; unexpected errors → 502.
- The plaintext secret never appears in the response, logs, or error messages.
- Stored-secret reuse is origin-pinned (scheme+host+port must match the stored server's url);
  decrypt failures surface as a friendly 422, never a raw 500.

## 5. Frontend

### 5a. `ui/frontend/src/lib/mcp.js` — pure helper

`mergeToolChoices(fetched, existing) -> {choices, extras}`:
- `choices`: fetched tools as `[{name, description, checked}]`, `checked` = name ∈ existing.
- `extras`: existing entries NOT in the fetched list (previously typed / renamed / offline) —
  kept as editable rows so nothing is silently dropped.
- Convergent: re-fetching never loses state.

### 5b. `McpServers.svelte` — Allowed tools section

- **Fetch tools** button beside the section label. Click → `api.mcpToolsPreview({url, transport,
  auth_type, auth_value, static_headers: headerRowsToDict(headerRows), server_id: editingId})`,
  spinner while pending, inline error banner on 422/502 (shows the ProbeError message).
- On success: checkbox list from `mergeToolChoices(fetched, [...toolRows-derived names])`,
  descriptions as hint text; `extras` remain as the old-style rows; "+ Add tool" stays.
- On Save: `allowed_tools = checked choice names ∪ listRowsToArray(extra rows)` (deduped).
- Picker state is form-local (reset with the form); no persistence of descriptions.
- Hint under the section keeps the "blank = all tools" semantics note.

api.js addition: `mcpToolsPreview: (body) => req('/api/mcp/tools/preview', {method:'POST', body: JSON.stringify(body)})`.

## 6. Testing

- **pytest**: `mcp_probe` (MockTransport: SSE-framed + plain-JSON responses, optional session id
  echo, auth header matrix per §2.2, static_headers overlay, 401 → ProbeError, JSON-RPC error
  object → ProbeError, sse transport rejected); `mcp_routes` preview endpoint (login gate,
  transport 422, stored-secret decrypt path via FakeStore, probe result passthrough, ProbeError
  → 422 with message, no secret in response).
- **node**: `mergeToolChoices` (checked mapping, extras preservation, convergence on re-fetch,
  dedup on save-merge).
- **e2e (local)**: add-form fetch against `https://mcp.deepwiki.com/mcp` pre-save → 3 tools
  ticked-able; edit an applied server with stored bearer secret + blank auth field → fetch
  succeeds via decrypt path; SSE transport → friendly message.

## 7. Risks / notes

- **SSRF surface**: the endpoint fetches an admin-supplied URL — same trust level as saving the
  server itself (which makes LiteLLM fetch it); login-gated; no response bodies echoed beyond
  parsed tool names/descriptions.
- **Confused-deputy guard**: the preview endpoint never sends a stored secret to an origin other
  than the one it was saved for (flagged by automated security review; the admin can still probe
  any host by entering a secret explicitly).
- Probe honors a 10s timeout; slow servers surface the timeout message rather than hanging the form.
- Descriptions are display-only; only names are stored in `allowed_tools`.
- Release: `feat:` → **1.34.0**; UI-only deploy to `.75` (hot feature, litellm untouched).
