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


def build_desired(items, resolve_key=None):
    """Return (desired_by_id, name_to_id, failed). desired is keyed by the rendered
    entry's model_info.id (defaults to item name, honors an explicit data.model_info.id).
    A credential that fails to resolve becomes a 'failed' entry (item skipped)."""
    desired: dict[str, dict] = {}
    name_to_id: dict[str, str] = {}
    failed: list[dict] = []
    for it in items:
        try:
            entry = render_model_entry(it, resolve_key)
        except KeyError as e:
            failed.append({"id": it["name"], "op": "resolve", "error": str(e)})
            continue
        mid = entry["model_info"]["id"]
        desired[mid] = entry
        name_to_id[it["name"]] = mid
    return desired, name_to_id, failed


def _is_already_exists(e) -> bool:
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = resp.text or ""
        except Exception:
            body = ""
    s = (str(e) + " " + body).lower()
    return ("unique constraint" in s or "already exists" in s
            or "failed to add model to db" in s)


async def reconcile_models(desired_items, live, client,
                           changed_item_names: set[str], creds_changed: set[str],
                           resolve_key: Callable[[str], Optional[str]]) -> dict[str, Any]:
    desired, name_to_id, failed = build_desired(desired_items, resolve_key)
    # Translate staged signals (item-names, credential-names) into model_info.id space.
    changed_ids = {name_to_id[n] for n in changed_item_names if n in name_to_id}
    force_ids = set()
    for it in desired_items:
        if it["name"] not in name_to_id:
            continue
        cred = (it["data"].get("litellm_params") or {}).get("litellm_credential_name")
        if cred and cred in creds_changed:
            force_ids.add(name_to_id[it["name"]])
    plan = diff_models(desired, live, changed_ids, force_ids)
    added = updated = deleted = 0
    for entry in plan["to_add"]:
        try:
            await client.add_model(entry); added += 1
        except Exception as e:
            if _is_already_exists(e):
                try:
                    await client.update_model(entry); updated += 1
                except Exception as e2:
                    failed.append({"id": entry["model_info"]["id"], "op": "add->update", "error": str(e2)})
            else:
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
