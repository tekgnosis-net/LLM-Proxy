from app.config_import import split_config


def test_split_known_sections_to_items_and_passthrough():
    cfg = {
        "model_list": [{"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {"mode": "chat"}}],
        "router_settings": {"routing_strategy": "least-busy", "num_retries": 2},
        "litellm_settings": {"cache": True},
        "general_settings": {"store_model_in_db": False, "master_key": "os.environ/LITELLM_MASTER_KEY"},
        "credential_list": [{"credential_name": "openai", "credential_values": {"api_key": "sk-REAL"}, "credential_info": {"provider": "openai"}}],
        "callbacks": ["langfuse"],            # unknown → passthrough
        "environment_variables": {"X": "1"},  # unknown → passthrough
    }
    items, passthrough = split_config(cfg, encrypt=lambda s: f"ENC({s})")
    by = {(i["kind"], i["name"]): i for i in items}
    assert by[("model", "gpt")]["data"]["litellm_params"]["model"] == "openai/gpt-4o"
    assert by[("router_setting", "routing_strategy")]["data"] == "least-busy"
    assert by[("router_setting", "num_retries")]["data"] == 2
    assert by[("litellm_setting", "cache")]["data"] is True
    assert by[("general_setting", "store_model_in_db")]["data"] is False
    assert by[("credential", "openai")]["data"] == {"provider": "openai", "value_encrypted": "ENC(sk-REAL)"}
    assert passthrough == {"callbacks": ["langfuse"], "environment_variables": {"X": "1"}}


def test_split_empty_config():
    items, passthrough = split_config({}, encrypt=lambda s: s)
    assert items == [] and passthrough == {}
