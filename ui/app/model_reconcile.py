from __future__ import annotations
from typing import Any, Callable, Optional

from app.config_render import render_model_entry


def _live_ids(live: list[dict]) -> set[str]:
    return {m.get("model_info", {}).get("id") for m in live if (m.get("model_info") or {}).get("id")}


def diff_models(desired: dict[str, dict], live: list[dict],
                changed_ids: set[str], force_ids: set[str]) -> dict[str, Any]:
    """Declarative add/delete by id (self-healing); update only for ids we know
    changed (staged 'changed') or whose credential rotated (force_ids)."""
    live_ids = _live_ids(live)
    desired_ids = set(desired)
    to_add = [desired[i] for i in sorted(desired_ids - live_ids)]
    to_delete = sorted(live_ids - desired_ids)
    upd_ids = (changed_ids | force_ids) & (desired_ids & live_ids)
    to_update = [desired[i] for i in sorted(upd_ids)]
    return {"to_add": to_add, "to_update": to_update, "to_delete": to_delete}


async def reconcile_models(desired_items: list[dict], live: list[dict], client,
                           changed_ids: set[str], force_ids: set[str],
                           resolve_key: Callable[[str], Optional[str]]) -> dict[str, Any]:
    desired: dict[str, dict] = {}
    failed: list[dict] = []
    for it in desired_items:
        try:
            desired[it["name"]] = render_model_entry(it, resolve_key)
        except KeyError as e:
            failed.append({"id": it["name"], "op": "resolve", "error": str(e)})
    plan = diff_models(desired, live, changed_ids, force_ids)
    added = updated = deleted = 0
    for entry in plan["to_add"]:
        try:
            await client.add_model(entry); added += 1
        except Exception as e:
            failed.append({"id": entry["model_info"]["id"], "op": "add", "error": str(e)})
    for entry in plan["to_update"]:
        try:
            await client.update_model(entry); updated += 1
        except Exception as e:
            failed.append({"id": entry["model_info"]["id"], "op": "update", "error": str(e)})
    for mid in plan["to_delete"]:
        try:
            await client.delete_model(mid); deleted += 1
        except Exception as e:
            failed.append({"id": mid, "op": "delete", "error": str(e)})
    return {"added": added, "updated": updated, "deleted": deleted, "failed": failed}
