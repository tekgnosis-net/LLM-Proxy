from __future__ import annotations
import copy
from typing import Any, Callable, Optional

_SECTION_BY_KIND = {"router_setting": "router_settings", "litellm_setting": "litellm_settings",
                    "general_setting": "general_settings"}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def effective(applied: list[dict], staged: list[dict]) -> list[dict]:
    """Overlay staged onto applied. Returns items with a `flag` (None=clean,
    'new'/'changed'/'deleted'). Deleted items are KEPT (marked) so the UI can strike them."""
    out: dict[tuple, dict] = {}
    for it in applied:
        out[(it["kind"], it["name"])] = {**it, "flag": None}
    for st in staged:
        key = (st["kind"], st["name"])
        flag = st.get("flag")
        if flag == "deleted":
            base = out.get(key, {"kind": st["kind"], "name": st["name"], "data": st.get("data")})
            out[key] = {**base, "flag": "deleted"}
        else:  # new | changed
            out[key] = {"kind": st["kind"], "name": st["name"], "data": st["data"], "flag": flag}
    return list(out.values())


def render_model_entry(it, resolve_key=None):
    """Build a LiteLLM model entry from a ui_config model item. model_info.id
    defaults to the item name (UUID). When resolve_key is given, inline the
    credential key (hybrid path): litellm_credential_name -> api_key."""
    data, name = it["data"], it["name"]
    entry = {"model_name": data.get("model_name", name)}
    mi = dict(data.get("model_info") or {})
    mi.setdefault("id", name)
    entry.update({k: v for k, v in data.items() if k not in ("model_name", "model_info")})
    entry["model_info"] = mi
    if resolve_key is not None:
        lp = dict(entry.get("litellm_params") or {})
        cred_name = lp.pop("litellm_credential_name", None)
        if cred_name:
            key = resolve_key(cred_name)
            if key is None:
                raise KeyError(f"credential {cred_name!r} not found")
            lp["api_key"] = key
        entry["litellm_params"] = lp
    return entry


def render_config(items, decrypt, hybrid=False):
    base = {}
    model_list, credential_list = [], []
    sections = {}
    for it in items:
        if it.get("flag") == "deleted":
            continue
        kind, name, data = it["kind"], it["name"], it["data"]
        if kind == "passthrough":
            base = copy.deepcopy(data) if isinstance(data, dict) else {}
        elif kind == "model":
            if not hybrid:
                model_list.append(render_model_entry(it))
        elif kind == "credential":
            if not hybrid:
                credential_list.append({"credential_name": name,
                                        "credential_values": {"api_key": decrypt(data.get("value_encrypted"))},
                                        "credential_info": {"provider": data.get("provider")}})
        elif kind in _SECTION_BY_KIND:
            sections.setdefault(_SECTION_BY_KIND[kind], {})[name] = data
    cfg = base
    for sec, kv in sections.items():
        cfg[sec] = _deep_merge(base.get(sec, {}), kv) if isinstance(base.get(sec), dict) else dict(kv)
    if hybrid:
        cfg.setdefault("model_list", [])      # empty: LiteLLM serves DB models only; no credential_list
    else:
        if model_list:
            cfg["model_list"] = model_list
        else:
            cfg.setdefault("model_list", [])
        if credential_list:
            cfg["credential_list"] = credential_list
    return cfg


def redact_rendered(cfg: dict) -> dict:
    cl = cfg.get("credential_list")
    if isinstance(cl, list):
        cfg = {**cfg, "credential_list": [{**c, "credential_values": {k: "***" for k in (c.get("credential_values") or {})}} for c in cl]}
    return cfg
