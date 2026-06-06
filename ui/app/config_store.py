from __future__ import annotations
import yaml
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, field_validator, model_validator

VALID_ROUTING_STRATEGIES = {
    "simple-shuffle", "least-busy", "usage-based-routing",
    "usage-based-routing-v2", "latency-based-routing", "cost-based-routing",
}
# ssl keys that trigger LiteLLM bug #10949 — never allowed in cache_params
FORBIDDEN_CACHE_KEYS = {"ssl", "ssl_check_hostname"}


class ConfigError(ValueError):
    pass


class LitellmParams(BaseModel, extra="allow"):
    model: str


class ModelEntry(BaseModel, extra="allow"):
    model_name: str
    litellm_params: LitellmParams


class RouterSettings(BaseModel, extra="allow"):
    routing_strategy: Optional[str] = None

    @field_validator("routing_strategy")
    @classmethod
    def _strategy(cls, v):
        if v is not None and v not in VALID_ROUTING_STRATEGIES:
            raise ValueError(
                f"invalid routing_strategy {v!r}; must be one of "
                f"{sorted(VALID_ROUTING_STRATEGIES)} (note: 'lowest-cost' is not valid)"
            )
        return v


class CacheParams(BaseModel, extra="allow"):
    type: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _no_ssl(cls, data):
        if isinstance(data, dict):
            for k in FORBIDDEN_CACHE_KEYS:
                if k in data:
                    raise ValueError(
                        f"cache_params must not contain {k!r} "
                        "(LiteLLM bug #10949: any ssl key forces a TLS "
                        "handshake that hangs against plain Valkey)"
                    )
        return data


class LitellmSettings(BaseModel, extra="allow"):
    cache: Optional[bool] = None
    cache_params: Optional[CacheParams] = None


class ProxyConfig(BaseModel, extra="allow"):
    general_settings: dict[str, Any] = {}
    litellm_settings: LitellmSettings = LitellmSettings()
    router_settings: RouterSettings = RouterSettings()
    model_list: list[ModelEntry] = []


def validate_config(raw: dict) -> "ProxyConfig":
    """Validate a candidate config dict (incl. guardrails). Raises ConfigError."""
    ls = raw.get("litellm_settings")
    cache_params = ls.get("cache_params") if isinstance(ls, dict) else None
    if isinstance(cache_params, dict):
        for k in FORBIDDEN_CACHE_KEYS:
            if k in cache_params:
                raise ConfigError(f"cache_params contains forbidden key {k!r} (LiteLLM bug #10949)")
    try:
        return ProxyConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(str(e)) from e


def load_config(path: str) -> ProxyConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    return validate_config(raw)
