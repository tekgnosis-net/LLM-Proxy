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


def render_config(items: list[dict], decrypt: Callable[[Any], str]) -> dict:
    """Pure: assemble a config.yaml dict from effective items. The kind='passthrough'
    item (if any) is the lowest-precedence base; managed sections override it. `decrypt`
    turns a credential's value_encrypted into the plaintext api_key. Deleted items excluded."""
    base: dict = {}
    model_list, credential_list = [], []
    sections: dict[str, dict] = {}
    for it in items:
        if it.get("flag") == "deleted":
            continue
        kind, name, data = it["kind"], it["name"], it["data"]
        if kind == "passthrough":
            base = copy.deepcopy(data) if isinstance(data, dict) else {}
        elif kind == "model":
            model_list.append({"model_name": name, **data})
        elif kind == "credential":
            credential_list.append({"credential_name": name,
                                    "credential_values": {"api_key": decrypt(data.get("value_encrypted"))},
                                    "credential_info": {"provider": data.get("provider")}})
        elif kind in _SECTION_BY_KIND:
            sections.setdefault(_SECTION_BY_KIND[kind], {})[name] = data
    cfg = base
    for sec, kv in sections.items():
        cfg[sec] = _deep_merge(base.get(sec, {}), kv) if isinstance(base.get(sec), dict) else dict(kv)
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
