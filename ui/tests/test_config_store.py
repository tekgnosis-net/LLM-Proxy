import pytest
import yaml
from app.config_store import load_config, ConfigError, VALID_ROUTING_STRATEGIES


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
