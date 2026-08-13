from __future__ import annotations
import uuid
from typing import Callable

_KNOWN = {"model_list", "router_settings", "litellm_settings", "general_settings",
          "credential_list", "mcp_servers"}
_DICT_SECTION_KIND = {"router_settings": "router_setting", "litellm_settings": "litellm_setting",
                      "general_settings": "general_setting"}

def split_config(cfg: dict, encrypt: Callable[[str], str]) -> tuple[list[dict], dict]:
    """Pure: split a config.yaml dict into typed items + a passthrough dict (unknown keys).
    Credential api_keys are encrypted via `encrypt`."""
    items: list[dict] = []
    for m in (cfg.get("model_list") or []):
        items.append({"kind": "model", "name": str(uuid.uuid4()), "data": dict(m)})
    for sec, kind in _DICT_SECTION_KIND.items():
        for key, val in (cfg.get(sec) or {}).items():
            items.append({"kind": kind, "name": key, "data": val})
    for c in (cfg.get("credential_list") or []):
        api_key = (c.get("credential_values") or {}).get("api_key", "")
        provider = (c.get("credential_info") or {}).get("provider")
        items.append({"kind": "credential", "name": c.get("credential_name"),
                      "data": {"provider": provider, "value_encrypted": encrypt(api_key)}})
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
    passthrough = {k: v for k, v in cfg.items() if k not in _KNOWN}
    return items, passthrough
