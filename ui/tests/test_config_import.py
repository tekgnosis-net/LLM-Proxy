import uuid as _uuid

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
    # model items are now keyed by uuid; find by kind + assert data.model_name
    model_items = [i for i in items if i["kind"] == "model"]
    assert len(model_items) == 1
    m = model_items[0]
    _uuid.UUID(m["name"])                                              # name is a valid uuid
    assert m["data"]["model_name"] == "gpt"
    assert m["data"]["litellm_params"]["model"] == "openai/gpt-4o"
    assert by[("router_setting", "routing_strategy")]["data"] == "least-busy"
    assert by[("router_setting", "num_retries")]["data"] == 2
    assert by[("litellm_setting", "cache")]["data"] is True
    assert by[("general_setting", "store_model_in_db")]["data"] is False
    assert by[("credential", "openai")]["data"] == {"provider": "openai", "value_encrypted": "ENC(sk-REAL)"}
    assert passthrough == {"callbacks": ["langfuse"], "environment_variables": {"X": "1"}}


def test_split_empty_config():
    items, passthrough = split_config({}, encrypt=lambda s: s)
    assert items == [] and passthrough == {}


def test_split_model_gets_uuid_name_and_keeps_model_name():
    import uuid
    from app.config_import import split_config
    cfg = {"model_list": [{"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}}]}
    items, _ = split_config(cfg, encrypt=lambda s: s)
    m = [i for i in items if i["kind"] == "model"][0]
    uuid.UUID(m["name"])                      # name is a valid uuid (raises if not)
    assert m["data"]["model_name"] == "gpt-4o"
    assert m["data"]["litellm_params"] == {"model": "openai/gpt-4o"}


def test_split_config_imports_mcp_servers():
    from app.config_import import split_config
    cfg = {"model_list": [], "mcp_servers": {
        "deepwiki": {"url": "https://mcp.deepwiki.com/mcp", "transport": "http"},
        "fc": {"url": "http://10.0.20.9:3002/mcp", "auth_type": "bearer_token", "auth_value": "tok"},
    }}
    items, passthrough = split_config(cfg, encrypt=lambda s: "ENC:" + s)
    mcp = {i["data"]["server_name"]: i for i in items if i["kind"] == "mcp_server"}
    assert set(mcp) == {"deepwiki", "fc"}
    assert mcp["deepwiki"]["data"]["transport"] == "http"
    assert mcp["fc"]["data"]["auth_value_encrypted"] == "ENC:tok"
    assert "auth_value" not in mcp["fc"]["data"]
    assert "mcp_servers" not in passthrough
