# MCP Gateway — Admin & Client Guide

> **Audience:** admins managing MCP servers through the UI, and developers
> connecting MCP clients (Claude Desktop, agents, the `mcp` Python/TS SDKs) to
> the proxy. For the general staged-apply model (Save/Apply/Discard, drift,
> config items), see [`admin-ui.md`](admin-ui.md) and
> [`admin-ui-guide.md`](admin-ui-guide.md) — this guide covers only what's
> specific to MCP.

LLM-Proxy unifies your MCP servers (self-hosted or hosted — firecrawl,
searxng, hindsight, deepwiki, …) behind **one gateway**. Agents connect to a
single endpoint with a virtual key and get a namespaced union of tools from
every MCP server that key is allowed to see, plus per-server health, usage,
and cost reporting in the admin UI.

**Live-verified against:** `ghcr.io/berriai/litellm:main-stable` (image dated
2026-06-05, re-checked 2026-08-13). LiteLLM's MCP surface is young —
re-verify field names/behavior against your pinned image on upgrade.

---

## Admin guide

### Adding and editing servers

**MCP Servers** (Configuration nav group) → **+ Add MCP server**. Fields:

| Field | Notes |
|---|---|
| **Server name** | `[A-Za-z0-9_-]+`, must be unique. Becomes the tool prefix (`firecrawl-scrape`) and the per-server endpoint path (`/firecrawl/mcp`). |
| **Description** | Optional, free text. |
| **Transport** | `Streamable HTTP` (`http`) or `SSE` (`sse`). v1 supports only these two — **stdio is out of scope** (see Known limitations). |
| **URL** | Required, `http://` or `https://`. **Stored and displayed in plain text** — see the URL-embedded-secret caveat below. |
| **Auth** | `None` / `API key` / `Bearer token` / `Basic`. Selecting one reveals an **Auth value** field. |
| **Static headers** | Key/value rows sent on every request to the upstream server. **Not encrypted** — the form warns "no secrets here, use Auth". |
| **Forwarded client headers** | Header *names* (no values) that are passed through from the calling MCP client to the upstream server. |
| **Allowed tools** | Optional allow-list; blank = every tool the server exposes is available. |
| **Allow all virtual keys** | When ticked, every key may call this server without an explicit grant (see Access control below). |
| **Default cost per tool call ($)** / **Per-tool cost overrides** | Optional spend-tracking cost model, attributed to `call_type='call_mcp_tool'` rows in the Activity feed and the MCP Usage card. |

#### Fetch tools (picker)

Next to **Allowed tools** is a **⟳ Fetch tools** button. It probes the URL/auth
currently sitting in the form directly — no `mcp` SDK, no server round-trip
through LiteLLM — so it works **before the server is saved or applied**, and
before any key can be granted access. **HTTP transport only**: an `SSE`
server can't be previewed this way (the direct probe needs a plain
request/response, which SSE doesn't give you) — Apply the server first and
use the row's **Tools** browser instead, or just type the tool names by hand.
A successful fetch turns any previously-typed names that match a live tool
into checkboxes (with the server's own descriptions as hint text); names you
typed that *aren't* on the server (a typo, a tool that's since been removed,
or one you're staging ahead of the server supporting it) are left alone as
plain editable rows underneath the checkboxes — a fetch doesn't discard a
name just because it isn't (yet) on the server; note that blank rows are
dropped and names are trimmed. On **Edit**, leaving **Auth value** blank and
clicking Fetch reuses the secret already stored for the host in the
currently saved (staged or applied) server entry — changing the URL host
requires re-entering the secret (origin-pinned: same scheme + hostname +
port). Point the URL field at a different host while Auth value is still
blank and the fetch is rejected with a "host differs" error instead of
silently sending your other server's credential somewhere else. A
redirecting server (often a missing trailing slash) is reported as an
error — use the exact URL.

Save stages the item (flag pill `new`/`changed`); nothing reaches LiteLLM
until you click **Apply**.

#### Hot apply

MCP items are applied **live, with no proxy restart** — they ride the same
hybrid-mode reconciler as models. The Apply-result banner reads:

```
Applied live — 0 added, 0 updated, 0 deleted; MCP — 1 added, 0 updated, 0 deleted
```

Verify a hot apply actually happened by comparing
`docker inspect --format '{{.State.StartedAt}}' <litellm-container>` before
and after — it should be **identical**. (If a *settings* item was staged in
the same Apply, that part of the pipeline may still restart the proxy — MCP
itself never triggers a restart.)

#### Editing a server that has a stored secret

Re-opening **Edit** on a server with `auth_type` set shows the Auth value
field with placeholder **`(unchanged — leave blank to keep)`**. Leave it
blank to keep the existing encrypted secret while changing any other field
(description, headers, cost, …); type a new value to rotate it.

> **Removing auth entirely (setting Auth back to `None`) is NOT reliable via
> Edit.** The UI correctly sends `auth_type: null` to LiteLLM, but LiteLLM's
> own `PUT /v1/mcp/server` handler inherits the previously-stored credential
> whenever the `credentials` field is omitted from the request — regardless
> of what `auth_type` says (a LiteLLM platform quirk, not a UI bug). **The
> reliable way to fully remove auth from a server is delete + re-add** (stage
> a delete and a fresh add with no auth, then one Apply) rather than editing
> the existing row down to `None`.

### Access control — the `ui-mcp` team model

Each MCP server is either:

- **`allow_all_keys: true`** — every virtual key may use it, no grant needed.
- **Per-key grant** (default, `allow_all_keys: false`) — a key must be
  explicitly granted the server on the **Virtual Keys** page.

LiteLLM's own ACL rule (verified live, not documented upstream): **a
teamless virtual key cannot be granted a non-`allow_all_keys` MCP server** —
`/key/generate`/`/key/update` returns a 403 ("Key is not in a team..."). To
work around this without weakening the ACL, the UI **owns and maintains a
dedicated team, `ui-mcp`**, behind the scenes:

- On every MCP Apply/Resync, the reconciler converges `ui-mcp`'s
  `object_permission.mcp_servers` to **the full set of master-managed MCP
  servers** (this is what makes the apply report show `team: synced` or
  `team: created` on the first MCP apply ever).
- **Granting** a server to a key: if the key has no team (or is already on
  `ui-mcp`), the UI sets `team_id: 'ui-mcp'` and the key's own
  `object_permission.mcp_servers` to the chosen subset.
- **Revoking all** MCP access from a key on `ui-mcp`: the UI sets
  `team_id: null` (**detaches** the key from the team) rather than leaving
  it on the team with an empty grant list. This matters because **a team-member
  key with an empty `mcp_servers` list fails OPEN and inherits the team's
  full scope** (live-verified) — leaving a "revoked" key on `ui-mcp` with
  `[]` would silently un-revoke it.
- **Foreign-team keys** (a key already on some other team) are left alone —
  the UI sends the grant but never changes `team_id` away from the foreign
  team. Whether the grant is actually usable then depends on whether that
  team's own `object_permission.mcp_servers` includes the server; the Keys
  page shows a warning hint in this case. This is a v1 limitation, not
  auto-resolved.

**Propagation delay:** grants and revokes write to the DB immediately
(`/key/info` reflects the change right away), but enforcement on the MCP
request path lags by **up to ~60 seconds** — LiteLLM's in-memory
`user_api_key_cache` has a 60s TTL and the admin update path does not
reliably bust it early for this specific auth path. Budget ~60–80s before
testing "did the grant/revoke take effect".

### Drift & Resync

Same UX as the Models screen. The header badge reads **In sync ✓** when the
UI's applied MCP items match LiteLLM's live server table (server_name,
transport, url, auth_type, allow_all_keys, allowed_tools, headers, cost —
**never credentials**, since LiteLLM redacts those on read). If someone
changes a server directly against the LiteLLM API (or a server is deleted
out-of-band), the badge switches to **⚠ N out of sync** with a **Resync to
proxy** button that re-adds/updates/deletes to converge — hot, no restart.
Resync re-creates a missing server under its **original master UUID**
(`server_id`), so existing key grants referencing that id remain valid
across a resync.

### Health & Usage

- **Test** button on a server row performs a live probe
  (`GET /v1/mcp/server/health?server_ids=`) and shows an ephemeral ✓/✗ next
  to the button. **Note:** on this LiteLLM build, a successful Test does
  **not** persist back to the server's `status` field (`GET /v1/mcp/server`
  keeps returning `status: null`) — only LiteLLM's own periodic background
  health-check job would update that persisted field, and it is not known to
  cover MCP servers on this build. In practice: trust the ✓/✗ next to Test as
  the live signal; don't expect the colored health dot to turn green from a
  manual Test alone.
- **Tools** button lists the tools a server exposes
  (`GET /api/mcp/tools?server_id=` → LiteLLM's `/mcp-rest/tools/list`) —
  disabled for a staged-but-unapplied (`new`) row, since tools come from the
  live registry. Tool names here are returned **bare** (`read_wiki_structure`),
  not prefixed — see the tool-namespacing note below.
- **Usage (last 30 days)** card aggregates `LiteLLM_SpendLogs` rows with
  `call_type='call_mcp_tool'`, grouped by server: calls, failures, spend,
  last call. See the spend-logging caveat below — only tool calls made
  through the real MCP protocol endpoint populate this.

---

## Client onboarding

Point MCP clients (the `mcp` Python/TS SDK, Claude Desktop, custom agents) at
the proxy's MCP surface:

| Endpoint | Use |
|---|---|
| `http://<proxy-host>:<port>/mcp` | Union of every server the calling key can see. Streamable HTTP. |
| `http://<proxy-host>:<port>/{server_name}/mcp` | Single-server endpoint — only that server's tools. |
| `http://<proxy-host>:<port>/sse` | Same union as `/mcp`, over SSE transport (for clients that speak SSE instead of streamable HTTP). |

**Auth header — `Authorization: Bearer <virtual key>`, not
`x-litellm-api-key`.** Every other LiteLLM REST endpoint accepts
`x-litellm-api-key`, but on this build the MCP protocol endpoints
(`/mcp`, `/{server_name}/mcp`) **reject** it — you get a 500 "Malformed API
Key passed in. Ensure Key has `Bearer ` prefix." Use a real `Authorization:
Bearer` header.

**Scoping to a subset of servers:** on the shared `/mcp` endpoint, add
`x-mcp-servers: name1,name2` to restrict the union to specific server names
(useful when a key is granted several servers but a given client should only
see some of them). Alternatively use the per-server endpoint
`/{server_name}/mcp` directly.

**Tool namespacing — bare vs. prefixed, depending on which surface you
call:**

- The **real MCP protocol endpoint** (`/mcp`, `/{server_name}/mcp`, `/sse` —
  what actual MCP clients speak) returns tools **prefixed**:
  `{server_name}-{tool_name}` (e.g. `deepwiki-read_wiki_structure`,
  separator configurable via `MCP_TOOL_PREFIX_SEPARATOR`). Call tools using
  the prefixed name.
- The admin UI's **REST convenience API** (`/mcp-rest/tools/list`,
  `/mcp-rest/tools/call` — what the MCP Servers page's Tools browser and
  `/api/mcp/tools` use internally) returns/expects **bare** tool names
  (`read_wiki_structure`, no prefix). Don't assume these two surfaces use the
  same tool-name format.

**Spend logging — use the real protocol endpoint, not `/mcp-rest/tools/call`.**
`/mcp-rest/tools/call` (the REST convenience endpoint) is a known upstream
bug: it never threads the logging object through to `execute_mcp_tool`, so
tool calls made through it write **no** `LiteLLM_SpendLogs` row at all — they
will never show up in the Activity feed or the MCP Usage card, regardless of
success. **Real MCP clients calling through `/mcp` or `/{server_name}/mcp`
ARE logged correctly** (`call_type='call_mcp_tool'`, full
`mcp_tool_call_metadata` with server/tool/arguments/result) — this is the
path the Activity feed and Usage card are built against, and the path any
production client should use anyway (it's the actual MCP protocol, not an
admin convenience shim).

Minimal example (Python `mcp` SDK, streamable HTTP):

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client(
        "http://<proxy-host>:<port>/deepwiki/mcp",
        headers={"Authorization": "Bearer sk-your-virtual-key"},
    ) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print([t.name for t in tools.tools])   # ['deepwiki-ask_question', ...]
            out = await s.call_tool(
                "deepwiki-read_wiki_structure", {"repoName": "org/repo"}
            )
            print(out)

asyncio.run(main())
```

### Known limitations (v1)

- **stdio transport is out of scope.** Only `http`/`sse` transports are
  supported end-to-end. If you bootstrap-import a `config.yaml` that has a
  `mcp_servers:` block with `command`/`args`/`env` (stdio) fields, those
  fields are **silently dropped** on import — only `server_name`,
  `transport`, `url`, `auth_type`, and the other v1-modeled fields survive.
- **URL-embedded secrets are stored and shown in plain text.** Some hosted
  MCP vendors embed an API key directly in the connection URL (e.g. a
  firecrawl-hosted URL). LiteLLM's `url` field — and this UI's table/export —
  store and display that URL as-is, in plaintext (both the master config DB
  and LiteLLM's own server table). This is accepted for v1 on the reasoning
  that the DB and export are admin-only surfaces; prefer header-based Auth or
  a self-hosted instance when you have the choice.
- **Static headers are plaintext**, always — the form warns "no secrets here,
  use Auth". Only the `Auth value` field is Fernet-encrypted at rest.
- **`mcp_access_groups` and per-key per-tool permissions
  (`mcp_tool_permissions`) are not exposed** by this UI (v1 scope is
  server-level grants only via `object_permission.mcp_servers`).
- **OAuth2 flows and BYOK are not supported** — Auth is limited to
  `api_key` / `bearer_token` / `basic` header credentials.

---

## See also

- [`admin-ui.md`](admin-ui.md) — the staged/Apply/drift architecture that MCP
  items participate in.
- [`admin-ui-guide.md`](admin-ui-guide.md) — per-screen field reference for
  the rest of the UI (Models, Keys, Routing, …).
- [`config-schema.md`](config-schema.md) — where `mcp_server` fits relative
  to the rendered `config.yaml` schema (it doesn't render into the file at
  all — see the note there).
