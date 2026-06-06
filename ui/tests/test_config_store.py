import pytest
import yaml
from app.config_store import load_config, ConfigError, CacheParams, VALID_ROUTING_STRATEGIES, ProxyConfig, validate_config


def write(tmp_path, data):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return str(p)


def test_loads_valid_config(tmp_path):
    path = write(tmp_path, {
        "general_settings": {"store_model_in_db": False},
        "litellm_settings": {"cache": True, "cache_params": {"type": "redis", "host": "valkey", "port": "6379"}},
        "router_settings": {"routing_strategy": "cost-based-routing"},
        "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini"}}],
    })
    cfg = load_config(path)
    assert cfg.router_settings.routing_strategy == "cost-based-routing"
    assert cfg.model_list[0].model_name == "cheap"


def test_rejects_ssl_key_in_cache_params(tmp_path):
    path = write(tmp_path, {
        "litellm_settings": {"cache": True, "cache_params": {"type": "redis", "host": "valkey", "port": "6379", "ssl": False}},
    })
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "ssl" in str(e.value).lower()


def test_rejects_invalid_routing_strategy(tmp_path):
    path = write(tmp_path, {"router_settings": {"routing_strategy": "lowest-cost"}})
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "lowest-cost" in str(e.value)


def test_cost_based_routing_is_valid():
    assert "cost-based-routing" in VALID_ROUTING_STRATEGIES
    assert "lowest-cost" not in VALID_ROUTING_STRATEGIES


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "nope.yaml"))


@pytest.mark.parametrize("key", ["ssl", "ssl_check_hostname"])
def test_cacheparams_model_rejects_ssl_directly(key):
    # locks the guardrail at the MODEL layer (would fail if the loop alone enforced it)
    with pytest.raises(Exception):
        CacheParams(**{key: False})


def test_string_cache_params_is_not_substring_matched(tmp_path):
    # 'use_ssl_please' must NOT trip the ssl guard; it's a malformed shape ->
    # should raise ConfigError (from pydantic), not a false "forbidden key ssl"
    p = tmp_path / "c.yaml"
    p.write_text("litellm_settings:\n  cache_params: use_ssl_please\n")
    with pytest.raises(ConfigError) as e:
        load_config(str(p))
    assert "forbidden key 'ssl'" not in str(e.value)


@pytest.mark.parametrize("body", ["- a\n- b\n", "just a string\n", "42\n"])
def test_malformed_root_raises_configerror_not_attributeerror(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body)
    with pytest.raises(ConfigError):
        load_config(str(p))


def test_litellm_settings_as_string_raises_configerror(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("litellm_settings: hello\n")
    with pytest.raises(ConfigError):
        load_config(str(p))


def test_model_entry_requires_model_in_litellm_params():
    with pytest.raises(Exception):
        ProxyConfig.model_validate({"model_list": [{"model_name": "x", "litellm_params": {}}]})


def test_model_entry_requires_model_name():
    with pytest.raises(Exception):
        ProxyConfig.model_validate({"model_list": [{"litellm_params": {"model": "openai/gpt-4o"}}]})


def test_unknown_keys_preserved_roundtrip():
    raw = {
        "litellm_settings": {"cache": True, "cache_params": {"type": "redis", "host": "valkey", "port": "6379"}},
        "router_settings": {"routing_strategy": "cost-based-routing", "num_retries": 5},
        "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini", "rpm": 100}}],
    }
    cfg = ProxyConfig.model_validate(raw)
    dumped = cfg.model_dump(exclude_none=True)
    assert dumped["router_settings"]["num_retries"] == 5
    assert dumped["model_list"][0]["litellm_params"]["rpm"] == 100


def test_validate_config_helper_raises_configerror_on_bad_routing():
    with pytest.raises(ConfigError):
        validate_config({"router_settings": {"routing_strategy": "lowest-cost"}})
