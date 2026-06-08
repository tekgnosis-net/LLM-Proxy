from __future__ import annotations
from typing import Callable

_KNOWN = {"model_list", "router_settings", "litellm_settings", "general_settings", "credential_list"}
_DICT_SECTION_KIND = {"router_settings": "router_setting", "litellm_settings": "litellm_setting",
                      "general_settings": "general_setting"}

def split_config(cfg: dict, encrypt: Callable[[str], str]) -> tuple[list[dict], dict]:
    """Pure: split a config.yaml dict into typed items + a passthrough dict (unknown keys).
    Credential api_keys are encrypted via `encrypt`."""
    items: list[dict] = []
    for m in (cfg.get("model_list") or []):
        name = m.get("model_name")
        data = {k: v for k, v in m.items() if k != "model_name"}
        items.append({"kind": "model", "name": name, "data": data})
    for sec, kind in _DICT_SECTION_KIND.items():
        for key, val in (cfg.get(sec) or {}).items():
            items.append({"kind": kind, "name": key, "data": val})
    for c in (cfg.get("credential_list") or []):
        api_key = (c.get("credential_values") or {}).get("api_key", "")
        provider = (c.get("credential_info") or {}).get("provider")
        items.append({"kind": "credential", "name": c.get("credential_name"),
                      "data": {"provider": provider, "value_encrypted": encrypt(api_key)}})
    passthrough = {k: v for k, v in cfg.items() if k not in _KNOWN}
    return items, passthrough
