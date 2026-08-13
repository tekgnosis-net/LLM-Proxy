from __future__ import annotations
from typing import Any, Callable, Optional

from app.model_reconcile import _is_already_exists

# LiteLLM redacts `credentials` in GET /v1/mcp/server, so live state is used for
# presence + non-secret content only. Credentials are (re)sent on every add/update
# where auth_type is set — idempotent; LiteLLM re-encrypts at rest with the salt key.

MCP_MANAGED_FIELDS = ("server_name", "description", "transport", "url", "auth_type",
                      "static_headers", "extra_headers", "allowed_tools",
                      "allow_all_keys", "mcp_info")

# Per-key grants for non-allow_all_keys servers require team membership on OSS
# (validate_key_mcp_servers_against_team — Task 1 report (f)). The UI manages ONE
# team whose MCP scope always equals every master-managed server id; granted keys
# join it with their own subset. CAUTION (live-proven): a team key with an EMPTY
# mcp_servers list inherits the FULL team scope — revoking must detach the key
# from the team (team_id: null), never leave it in the team with [].
MCP_TEAM_ID = "ui-mcp"


async def sync_mcp_team(client, desired_ids: set) -> str:
    """Converge the ui-mcp team's MCP scope to the master server set.
    Update-first (idempotent, no read); create on miss; 'skipped' when there is
    no scope to write and no team to update."""
    payload = {"team_id": MCP_TEAM_ID,
               "object_permission": {"mcp_servers": sorted(desired_ids)}}
    try:
        await client.update_team(payload)
        return "synced"
    except Exception:
        if not desired_ids:
            return "skipped"
        try:
            await client.new_team({**payload, "team_alias": "UI-managed MCP grants"})
            return "created"
        except Exception as e:
            raise RuntimeError(f"mcp team sync failed: {e}") from e


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
    """Managed-field comparison; credentials never compared (redacted in live).
    mcp_info compares by its mcp_server_cost_info sub-key only — LiteLLM decorates
    mcp_info with server_name/description server-side (Task 1 report (a)), which
    would otherwise false-drift every server."""
    out = []
    for f in MCP_MANAGED_FIELDS:
        if f == "mcp_info":
            d = _norm_deep((desired.get("mcp_info") or {}).get("mcp_server_cost_info"))
            live_ci = _norm_deep((live.get("mcp_info") or {}).get("mcp_server_cost_info"))
            if d != live_ci:
                out.append(f)
        elif _norm_deep(desired.get(f)) != _norm_deep(live.get(f)):
            out.append(f)
    return out


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
                failed.append({"id": it["name"], "name": d.get("server_name"), "op": "decrypt", "error": str(e)})
                continue
        desired[it["name"]] = payload
    return desired, failed


def diff_mcp(desired: dict[str, dict], live: list[dict], changed_ids: set[str],
             protected_ids: frozenset = frozenset()) -> dict[str, Any]:
    """Declarative add/delete by server_id (self-healing); update only ids known changed.
    protected_ids are excluded from to_delete — used to shield decrypt-failed items whose
    live counterpart must not be wiped out just because the local vault couldn't decrypt."""
    live_ids = {s.get("server_id") for s in live if s.get("server_id")}
    desired_ids = set(desired)
    return {
        "to_add": [desired[i] for i in sorted(desired_ids - live_ids)],
        "to_update": [desired[i] for i in sorted(changed_ids & desired_ids & live_ids)],
        "to_delete": sorted(live_ids - desired_ids - set(protected_ids)),
    }


async def reconcile_mcp(desired_items, live, client,
                        changed_item_names: set[str],
                        decrypt: Optional[Callable[[str], str]]) -> dict[str, Any]:
    desired, failed = build_desired(desired_items, decrypt)
    failed_ids = {f["id"] for f in failed}
    live_ids = {s.get("server_id") for s in live if s.get("server_id")}
    live_names = {s.get("server_id"): s.get("server_name") for s in live if s.get("server_id")}
    protected = failed_ids & live_ids
    plan = diff_mcp(desired, live, changed_item_names & set(desired), protected_ids=protected)
    added = updated = deleted = 0
    for entry in plan["to_add"]:
        try:
            await client.add_server(entry); added += 1
        except Exception as e:
            if _is_already_exists(e):
                try:
                    await client.update_server(entry); updated += 1
                except Exception as e2:
                    failed.append({"id": entry["server_id"], "name": entry.get("server_name"), "op": "add->update", "error": str(e2)})
            else:
                failed.append({"id": entry["server_id"], "name": entry.get("server_name"), "op": "add", "error": str(e)})
    for entry in plan["to_update"]:
        try:
            await client.update_server(entry); updated += 1
        except Exception as e:
            failed.append({"id": entry["server_id"], "name": entry.get("server_name"), "op": "update", "error": str(e)})
    for sid in plan["to_delete"]:
        try:
            await client.delete_server(sid); deleted += 1
        except Exception as e:
            failed.append({"id": sid, "name": live_names.get(sid), "op": "delete", "error": str(e)})
    out = {"added": added, "updated": updated, "deleted": deleted, "failed": failed}
    try:
        out["team"] = await sync_mcp_team(client, set(desired) | protected)
    except Exception as e:
        out["team"] = "error"
        failed.append({"id": MCP_TEAM_ID, "name": MCP_TEAM_ID, "op": "team_sync", "error": str(e)})
    return out
