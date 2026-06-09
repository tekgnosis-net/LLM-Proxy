from app.config_render import effective, render_config, redact_rendered


def test_effective_overlays_staged_flags():
    applied = [
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o"}}},
        {"kind": "router_setting", "name": "routing_strategy", "data": "simple-shuffle"},
    ]
    staged = [
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": "changed"},
        {"kind": "model", "name": "claude", "data": {"litellm_params": {"model": "anthropic/claude-3"}}, "flag": "new"},
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o"}}, "flag": "deleted"},
    ]
    eff = {(i["kind"], i["name"]): i for i in effective(applied, staged)}
    assert eff[("router_setting", "routing_strategy")]["data"] == "least-busy"
    assert eff[("router_setting", "routing_strategy")]["flag"] == "changed"
    assert eff[("model", "claude")]["flag"] == "new"
    assert eff[("model", "gpt")]["flag"] == "deleted"            # kept, marked
    assert eff[("model", "gpt")]["data"]["litellm_params"]["model"] == "openai/gpt-4o"


def test_render_groups_items_into_sections_and_decrypts_creds():
    items = [
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "openai"}, "model_info": {"mode": "chat"}}, "flag": None},
        {"kind": "credential", "name": "openai", "data": {"provider": "openai", "value_encrypted": "ENC"}, "flag": None},
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": None},
        {"kind": "litellm_setting", "name": "cache", "data": True, "flag": None},
        {"kind": "general_setting", "name": "store_model_in_db", "data": False, "flag": None},
        {"kind": "model", "name": "old", "data": {"litellm_params": {"model": "x/y"}}, "flag": "deleted"},
        {"kind": "passthrough", "name": "_", "data": {"litellm_settings": {"drop_params": True}}, "flag": None},
    ]
    cfg = render_config(items, decrypt=lambda b: "sk-REAL")
    assert cfg["router_settings"] == {"routing_strategy": "least-busy"}
    assert cfg["general_settings"] == {"store_model_in_db": False}
    assert cfg["litellm_settings"]["cache"] is True
    assert cfg["litellm_settings"]["drop_params"] is True          # passthrough deep-merged
    assert {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "openai"}, "model_info": {"mode": "chat"}} in cfg["model_list"]
    assert all(m["model_name"] != "old" for m in cfg["model_list"])  # deleted excluded
    assert cfg["credential_list"][0] == {"credential_name": "openai", "credential_values": {"api_key": "sk-REAL"}, "credential_info": {"provider": "openai"}}


def test_render_managed_wins_over_passthrough():
    items = [
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": None},
        {"kind": "passthrough", "name": "_", "data": {"router_settings": {"routing_strategy": "EVIL", "extra": 1}}, "flag": None},
    ]
    cfg = render_config(items, decrypt=lambda b: "")
    assert cfg["router_settings"]["routing_strategy"] == "least-busy"   # managed wins
    assert cfg["router_settings"]["extra"] == 1                          # passthrough extra key kept


def test_redact_masks_credential_values():
    cfg = {"credential_list": [{"credential_name": "x", "credential_values": {"api_key": "sk-REAL"}}]}
    assert redact_rendered(cfg)["credential_list"][0]["credential_values"]["api_key"] == "***"


def test_two_models_same_name_both_render():
    from app.config_render import render_config
    items = [
        {"kind": "model", "name": "id-a", "data": {"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}}},
        {"kind": "model", "name": "id-b", "data": {"model_name": "gpt-4o", "litellm_params": {"model": "azure/gpt-4o"}}},
    ]
    cfg = render_config(items, decrypt=lambda v: "")
    names = [m["model_name"] for m in cfg["model_list"]]
    assert names == ["gpt-4o", "gpt-4o"]
    assert {m["litellm_params"]["model"] for m in cfg["model_list"]} == {"openai/gpt-4o", "azure/gpt-4o"}
