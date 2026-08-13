# MCP Gateway (v3.27) — Design

**Date:** 2026-08-13
**Status:** Approved design, pending spec review
**Branch:** `v3.27-mcp-gateway`
**Depends on:** v3 master/servant config system (shipped), per-key passthrough pattern (1.32.0)

## 1. Goal

Unify the user's MCP servers — firecrawl (hosted + self-hosted), searxng, hindsight, and future
ones — behind LLM-Proxy. Agents connect to **one** gateway endpoint
(`http://10.0.20.75:8000/mcp`) with a **virtual key**, and get a namespaced union of tools from
every MCP server that key is allowed to see. Admins manage MCP servers in the UI with the same
staged Save→Apply workflow as models, get per-server health + usage reporting, and grant MCP
access per key.

## 2. Verified platform facts

All verified by inspecting source inside `ghcr.io/berriai/litellm:main-stable`
(image created 2026-06-05) — **not** from docs. Re-verify against the `.75` image in plan step 0
because `main-stable` floats.

| Fact | Evidence |
|---|---|
| Admin CRUD: `GET/POST/PUT /v1/mcp/server`, `GET/DELETE /v1/mcp/server/{server_id}`, `GET /v1/mcp/server/health?server_ids=` | `litellm/proxy/management_endpoints/mcp_management_endpoints.py` (router prefix `/v1/mcp`) |
| Tool discovery: `GET /mcp-rest/tools/list?server_id=` | `mcp_rest_api` docs + `rest_endpoints.py` |
| **Zero enterprise gating** in the MCP module and in `object_permission_utils.py` | `grep premium/enterprise` returns nothing |
| CRUD is **hot**: handlers call `global_mcp_server_manager.add_server / update_server / remove_server` + `reload_servers_from_database()` in-process | `mcp_management_endpoints.py:1339,1835,2166` |
| DB storage rides on `STORE_MODEL_IN_DB=true` (we already run it); servers reload from DB on boot, so restarts keep hot-applied state | `proxy_server.py` calls `reload_servers_from_database` |
| Request model `NewMCPServerRequest`: `server_id, server_name, alias, description, transport (sse default), auth_type, credentials, url, mcp_info, mcp_access_groups, allowed_tools, extra_headers, static_headers, allow_all_keys, command/args/env (stdio), oauth2 fields` | `litellm/proxy/_types.py:1253` |
| `credentials` is `MCPCredentials` TypedDict; for header auth the key is `auth_value` | `litellm/types/mcp.py:79` |
| `credentials` encrypted at rest with `LITELLM_SALT_KEY` (`encrypt_credentials`) | `_experimental/mcp_server/db.py:55` |
| `GET /v1/mcp/server` **redacts** `credentials` (set to `None`) | `_redact_mcp_credentials`, `mcp_management_endpoints.py:456` |
| `PUT` with `credentials` omitted **inherits** the stored secret (`_inherit_credentials_from_existing_server`) | `mcp_management_endpoints.py:573-596` |
| Per-key/team ACL: `object_permission: {mcp_servers: [...], mcp_access_groups: [...], mcp_tool_permissions: {...}}` on `/key/generate\|update`; keys without grants inherit team; enforcement via `MCPRequestHandler.get_allowed_mcp_servers` | `_types.py:991-993, 2019-2021` |
| `/key/update`: `object_permission` omitted → preserved; provided → merged into `LiteLLM_ObjectPermissionTable` row + upsert | `object_permission_utils.py:66-130` |
| Client surface: `app.mount("/mcp")`, `/{server_name}/mcp`, `/sse`; auth via `x-litellm-api-key: <virtual key>`; optional `x-mcp-servers` scoping; tools namespaced `{server}-{tool}` | `_experimental/mcp_server/server.py:3224-3227` |
| Spend logging: `call_type='call_mcp_tool'` / `'list_mcp_tools'` rows in `LiteLLM_SpendLogs`; metadata carries `mcp_tool_call_metadata` (`name, arguments, result, mcp_server_name`) | `types/utils.py:446,2536`; `spend_tracking_utils.py:402` |
| Per-tool costs: `mcp_info.mcp_server_cost_info = {default_cost_per_query, tool_name_to_cost_per_query}` | mcp_cost docs, `MCPInfo` type |
| Health columns persisted on `LiteLLM_MCPServerTable`: `status, last_health_check, health_check_error` | `schema.prisma:314-317` |
| Image has `node` + `npx`; **no `uvx`** | container check |

## 3. Scope

**v1 (this spec):**
- New config kind `mcp_server`, staged + hot-applied (HTTP and SSE transports only).
- MCP Servers UI page: create/edit/delete with flag pills, health badges + Test, tools browser,
  per-server usage summary, per-tool cost config.
- Per-key ACL picker on the Keys page via `object_permission.mcp_servers`.
- Activity feed: MCP rows (badge, server·tool) + detail pane arguments/result.
- Drift/resync/integrity/export coverage for the new kind.

**Out of scope (v2 candidates):** stdio transport, OAuth2 flows, `mcp_access_groups`,
`mcp_tool_permissions` (per-key per-tool), `litellm_settings.mcp_aliases`, team-level grants,
BYOK, custom cost hooks.

## 4. Data model — kind `mcp_server`

Item name = **UUID**, passed to LiteLLM as `server_id` (stable identity; renames don't
delete+recreate — same rationale as the model-identity migration). `data`:

```json
{
  "server_name": "firecrawl",            // required, unique among mcp_server items; shown in UI,
                                          // used in tool prefix and /{server_name}/mcp mount.
                                          // Charset: [a-zA-Z0-9_-]+ (validated on stage)
  "description": "Self-hosted firecrawl", // optional
  "transport": "http",                    // "http" | "sse" (v1)
  "url": "http://10.0.20.x:3002/mcp",     // required
  "auth_type": "bearer_token",            // null | "api_key" | "bearer_token" | "basic"
  "auth_value_encrypted": "<fernet>",     // present only when auth_type != null; Fernet
                                          // ciphertext (CREDENTIALS_KEY vault)
  "static_headers": {},                   // optional {header: value}; PLAINTEXT — UI warns
                                          // "secrets belong in Auth, not here"
  "extra_headers": [],                    // optional [header-name] forwarded from client
  "allowed_tools": [],                    // optional allow-list; empty = all tools
  "allow_all_keys": false,                // true = every virtual key may use this server
  "mcp_info": {
    "mcp_server_cost_info": {
      "default_cost_per_query": 0.0,
      "tool_name_to_cost_per_query": {}
    }
  }
}
```

**Staging semantics** (mirrors `credential` kind):
- A new `_mcp_server_data()` helper in `config_v3_routes.py` (analogue of `_credential_data()`,
  L66-82): on stage, plaintext `auth_value` from the form is encrypted to
  `auth_value_encrypted`; **blank means keep** the previously stored ciphertext (edit without
  re-typing the secret); switching `auth_type` to null drops it.
- Redaction: `_redact_item()` (config_v3_routes.py:42-46) and `redact_rendered()`
  (config_render.py:93-97) gain the `auth_value_encrypted` field → `"__redacted__"` on read.
  `GET /api/config/export` keeps ciphertext (backup parity with `credential` items).
- Validation on stage: `server_name` required + charset + unique; `url` required http(s);
  `transport` ∈ {http, sse}; cost values numeric ≥ 0.

## 5. Apply pipeline

### 5a. New client — `ui/app/mcp_client.py`

Same shape as `models_client.py` (constructor `(base_url, master_key, transport=None)`, 15s
timeout, `Authorization: Bearer <master_key>`):

- `list_servers()` → `GET /v1/mcp/server` (credentials come back redacted — never compare them)
- `add_server(payload)` → `POST /v1/mcp/server`
- `update_server(payload)` → `PUT /v1/mcp/server` (payload includes `server_id`)
- `delete_server(server_id)` → `DELETE /v1/mcp/server/{server_id}`
- `health(server_ids=None)` → `GET /v1/mcp/server/health`
- `list_tools(server_id)` → `GET /mcp-rest/tools/list?server_id=`

Wire payload built from an item (in `mcp_reconcile.py`, see 5b):

```python
{
  "server_id": item_name,                # our UUID
  "server_name": d["server_name"],
  "description": d.get("description"),
  "transport": d["transport"],
  "url": d["url"],
  "auth_type": d.get("auth_type"),
  "credentials": {"auth_value": decrypt(d["auth_value_encrypted"])},  # whenever auth_type is
                                          # set; omitted when auth_type is null. (LiteLLM would
                                          # inherit on omission — see §2 — but always sending is
                                          # simpler and idempotent; it re-encrypts at rest)
  "static_headers": d.get("static_headers") or None,
  "extra_headers": d.get("extra_headers") or None,
  "allowed_tools": d.get("allowed_tools") or None,
  "allow_all_keys": d.get("allow_all_keys", False),
  "mcp_info": d.get("mcp_info") or None,
}
```

### 5b. New reconciler — `ui/app/mcp_reconcile.py`

Structural mirror of `model_reconcile.py`:

- `diff_mcp(staged)` → sets of changed ids from the staged rows (adds/changes/deletes by flag).
- `build_desired(items, decrypt)` → `{server_id: payload}` from master `mcp_server` items.
- `reconcile_mcp(client, desired, changed_ids)`:
  - live = `{s.server_id: s for s in list_servers()}`
  - **add**: desired ids not in live → `add_server` (with credentials when auth present).
    An "already exists" collision converts to update (mirror `_is_already_exists`).
  - **update**: ids in `changed_ids` that exist live → `update_server` with the full §5a
    payload (credentials included whenever auth_type is set).
  - **delete**: live ids not in desired → `delete_server`.
  - Returns `{added: [...], updated: [...], deleted: [...], failed: [{id, error}]}` for the
    apply report.

### 5c. Engine wiring — `config_engine.py`

- `mcp_server` is **NOT** added to `_RESTART_KINDS` (L11). MCP edits never restart litellm.
- Hybrid branch: alongside `reconcile_models` (L141-145), compute
  `mcp_changed = any(s["kind"] == "mcp_server" for s in staged)`; when true run
  `reconcile_mcp`; add `"mcp": <report>` to the apply result dict. (Resync calls
  `reconcile_mcp` directly — §5d.)
  Frontend `configStore.svelte.js` result handling (L34-50) surfaces failures the same way
  model failures surface (banner; state already folded → drift is the recovery path).
- **Non-hybrid guard**: in `apply_config`, if staged contains `mcp_server` and
  `hybrid=False`, raise `ApplyError("mcp: requires STORE_MODEL_IN_DB=true (hot apply)")` —
  prevents the silent-no-op trap (survey gotcha #2). Our deployment always runs hybrid.
- Credential-rotation coupling: MCP items reference no `credential` items (auth is embedded),
  so `creds_changed` logic is untouched.

### 5d. Render / import / integrity / drift

- `config_render.py`: `render_config()` emits **nothing** for `mcp_server` (both modes) —
  config.yaml never contains MCP servers, so no new secret-materialization path and no
  `SECRET_FIELDS` change. `_SECTION_BY_KIND` gains no entry; instead the explicit kind branch
  is a no-op with a comment.
- `config_import.py`: `_KNOWN` gains `"mcp_servers"`; `split_config()` converts a
  `mcp_servers: {name: {...}}` YAML block into `mcp_server` items (UUID names, `server_name`
  = YAML key, `auth_value` encrypted via the passed `encrypt` fn) so a bootstrap import doesn't
  swallow the block into the `passthrough` singleton.
- `config_integrity.py`: new `mcp_server_names(items)` helper (analogue of `group_names`)
  returning `{server_name} ∪ {item uuid}`; consumed by key validation (§6). Integrity report
  (`/api/config/integrity`) gains `key_mcp_orphans`: keys whose `object_permission.mcp_servers`
  reference ids absent from master (read via `keys_client.list_keys`, same as `key_orphans`).
- Drift (`config_v3_routes.py:191-215`): new `mcp` section — compare master
  (id, server_name, transport, url, auth_type, allow_all_keys, allowed_tools, static/extra
  headers, mcp_info) vs `list_servers()` output, **excluding credentials** (redacted live).
  Extra live servers, missing servers, and field mismatches are reported.
  `/api/config/resync` (L176-189) reconverges by running `reconcile_mcp` with
  `changed_ids = mismatched ids`.

## 6. Per-key access control

**Wire format** (LiteLLM-native, no metadata hack needed this time):

```json
POST /key/generate | /key/update
{ ..., "object_permission": { "mcp_servers": ["<server_id-uuid>", ...] } }
```

- Store **server_id UUIDs** (stable across rename). Empty list = no MCP access (unless a
  server has `allow_all_keys=true`, which bypasses per-key grants).
- `/key/update` semantics (verified): `object_permission` omitted → preserved; provided →
  merged field-wise + upserted. The UI always sends the complete `mcp_servers` list when the
  picker changed, and omits `object_permission` when untouched.
- `keys_routes.py` continues to forward payloads verbatim. `_validate_key_refs()` (L22-43)
  gains: every id in `object_permission.mcp_servers` must be in `mcp_server_names()` of the
  effective config → 422 `unknown MCP server` otherwise.
- **Keys list**: `list_keys` already uses `return_full_object=true`; confirm
  `object_permission` is included in the response (plan step 0) — it populates the picker on
  edit. If absent, fetch via `/key/info` fallback.

**Plan step 0 — live proof (local stack first, `.75` only if local build lacks enforcement):**
1. Stage+apply one MCP server (deepwiki, no auth). 2. Key WITHOUT grant → `tools/list` via
`/mcp` returns no tools or 403. 3. Key WITH `object_permission.mcp_servers=[id]` → tools
visible, tool call succeeds, SpendLogs row `call_type='call_mcp_tool'` appears. 4. Update key
to `mcp_servers: []` → access revoked (verifies clear-to-empty). 5. `object_permission`
returned by `/key/list?return_full_object=true`. Abort → redesign (fallback: server-level
`allow_all_keys` + x-mcp-servers scoping) if any check fails hard.

## 7. Backend endpoints — new `ui/app/routes/mcp_routes.py`

All behind `login_required`, master key server-side, factory seam `make_mcp_client()`:

| Endpoint | Behavior |
|---|---|
| `GET /api/mcp/health?server_ids=` | Proxy `/v1/mcp/server/health`; merge persisted `status/last_health_check/health_check_error` from `list_servers()` |
| `GET /api/mcp/tools?server_id=` | Proxy `/mcp-rest/tools/list`; returns `[{name, description}]` |
| `GET /api/mcp/usage?days=N` | asyncpg SQL (usage_routes conventions: fresh conn, `_iso_utc`): per-server calls, spend, errors, last call |

Usage SQL sketch (verify `metadata` column type in plan; existing code notes it arrives as a
JSON string, so cast):

```sql
SELECT COALESCE(l.metadata::jsonb #>> '{mcp_tool_call_metadata,mcp_server_name}', '(unknown)') AS server,
       count(*) AS calls, sum(l.spend) AS spend, max(l."startTime") AS last_call
FROM "LiteLLM_SpendLogs" l
WHERE l.call_type = 'call_mcp_tool' AND l."startTime" >= $1
GROUP BY 1 ORDER BY calls DESC
```

## 8. Frontend

### 8a. New page — `McpServers.svelte` (store-backed, Configuration nav group)

`App.svelte`: import + nav button `MCP Servers` (after Caching) + `{:else if screen==='mcp'}`
branch passing `{store}`. Copy the standard scoped-CSS block (`.page/.card/label/table/
.banner/.flag-tag/.row-*`) — Svelte scopes per component.

- **Table**: server_name, transport, URL (secret-bearing URLs shown full — see §11), auth type
  badge, allow-all-keys badge, health badge (`healthy/unhealthy/unknown` + tooltip with last
  check + error), flag pill, Edit/Delete(→undo) actions. Health loads on mount from
  `/api/mcp/health` (non-blocking; badge shows `…` until resolved).
- **Create/edit card** (`.card.add` pattern from Models.svelte): name, description,
  transport select, URL, auth type select + secret input (placeholder `(unchanged)` when
  editing an item that has a stored secret), static headers (key/value rows — `+ Add header`),
  extra headers (name rows), allowed tools (comma/rows), allow_all_keys checkbox,
  cost section (default cost per query + per-tool rows `tool → cost`).
- **Tools browser**: per-row expand button → `GET /api/mcp/tools?server_id=` → name +
  description list; shows actionable error when the server is unreachable. Disabled for
  staged-but-unapplied rows (tools come from the live registry).
- **Usage summary card**: `GET /api/mcp/usage?days=30` table (server, calls, spend via
  `money()`, last call via `fmtDateTime()`).
- **Apply/drift**: global Apply bar already handles staged counts; after apply, re-load
  health; drift badge + Resync reuse the Models.svelte pattern (L193-220).

### 8b. `lib/mcp.js` — pure helpers (node-testable, passthrough.js pattern)

`headerRowsToDict(rows)` / `dictToHeaderRows(obj)`; `toolCostRowsToDict` / `dictToToolCostRows`
(numeric coercion, drop blanks); `listRowsToArray` / `arrayToListRows` (trim/dedup for
extra_headers + allowed_tools); `validateMcpForm(form)` → error string or null.

### 8c. Keys page picker — `Keys.svelte`

- State: `mcpGrants = $state([])` (server ids) + derived list of applied `mcp_server` items —
  **needs the `store` prop added to `Keys` in App.svelte** (currently not passed; Keys gains
  `let { store } = $props()`; store is read-only here — grants are runtime data like the key
  itself).
- UI: checkbox row per master MCP server (label = server_name), section titled
  "MCP servers"; hidden when no MCP servers configured.
- `editKey()`: populate from `k.object_permission?.mcp_servers ?? []`.
- `buildKeyFields()`: include `object_permission: {mcp_servers: mcpGrants}` **only when the
  selection changed** (avoids churning the permission row on unrelated edits); include the
  empty list when the user cleared grants.

### 8d. Activity feed — `ActivityFeed.svelte` + `usage_routes.py`

- `_ACTIVITY_SELECT` gains `l.call_type` and the two metadata extractions
  (`mcp_server_name`, tool `name`) as `mcp_server` / `mcp_tool` columns.
- Row rendering: when `call_type = 'call_mcp_tool'` → `MCP` badge (reuse `.flag-tag` styling)
  and `{mcp_server} · {mcp_tool}` displayed in the Model column (currently empty for MCP rows);
  provider column shows `mcp`.
- Filter: the existing status/model filter row gains type filter `All / LLM / MCP`
  (MCP: `call_type IN ('call_mcp_tool','list_mcp_tools')`; LLM: `NOT IN` the same set).
- Detail pane (`/api/usage/tx/{id}`): when the row is MCP, render an "MCP tool call" section —
  tool name, server, arguments (pretty JSON), result (pretty JSON, collapsed if large) from
  `metadata.mcp_tool_call_metadata`.

## 9. Client onboarding surface (docs)

`docs/user-guide` addition (post-implementation): connect agents to
`http://10.0.20.75:8000/mcp` with headers `x-litellm-api-key: <virtual key>` (+ optional
`x-mcp-servers: server_a,server_b` scoping); per-server endpoint `/{server_name}/mcp`; tools
appear as `{server_name}-{tool}`.

## 10. Testing

- **pytest** (`ui/.venv/bin/python -m pytest`): `mcp_client` (httpx MockTransport),
  `mcp_reconcile` (add/update/delete/collision/credential-omission matrix), engine wiring
  (mcp staged → reconcile called, restart NOT triggered; non-hybrid guard), stage validation +
  blank-means-keep + redaction, import of `mcp_servers:` YAML, drift/resync mcp section,
  `_validate_key_refs` mcp branch, `/api/mcp/*` routes.
- **node** (`node --input-type=module -e`): `lib/mcp.js` helpers.
- **Local e2e**: stack + deepwiki (`https://mcp.deepwiki.com/mcp`, no auth, http transport) —
  full loop: stage → apply (hot; litellm `StartedAt` unchanged) → health OK → tools listed →
  key-scoped call via `/mcp` → Activity row shows MCP badge → usage card shows the call.
- **`.75` validation targets** (user's real servers, all http/sse): firecrawl hosted
  (URL-embedded key), firecrawl self-hosted, searxng, hindsight. At least one must exercise
  `auth_type` + secret storage end-to-end.

## 11. Risks & accepted limitations

1. **`main-stable` floats.** All §2 facts re-verified in plan step 0 against the running
   image; the spec pins field names/paths as of 2026-08-13.
2. **URL-embedded secrets** (firecrawl hosted embeds the API key in the URL path): `url` is
   stored plaintext in master and in LiteLLM's table, and appears in exports and the UI table.
   Accepted for v1 (DB and export are admin-only); UI hint recommends header auth or
   self-hosted where possible. Revisit (URL redaction) if it becomes a problem.
3. **`static_headers` are plaintext** — the form warns; secrets belong in the Auth field.
4. **Post-commit partial failures fold anyway** (system invariant): recovery is the extended
   drift/resync, surfaced by the existing drift badge.
5. **Health of staged-but-unapplied servers is unknown** — badges apply to live state only.
6. **Restart-path interplay**: a settings restart reloads MCP servers from LiteLLM's DB —
   no MCP re-apply needed; `reload_and_verify` still only probes models (acceptable: MCP apply
   is hot and reports its own per-server failures).

## 12. Release & deploy

`feat:` → semantic-release **1.33.0** → GHCR → pin bump → `.75` UI-only deploy
(`docker compose up -d --no-deps llm-proxy-ui`; litellm untouched — MCP apply is hot).
Onboard the real servers via the UI on `.75`, then update memory topic file.
