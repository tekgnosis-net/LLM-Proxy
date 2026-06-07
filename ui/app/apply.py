from __future__ import annotations
from app.config_store import (load_config, ConfigError, ProxyConfig,
                              seed_baseline_if_missing, promote_baseline, restore_baseline)
from app.reloader import ReloadError


class ApplyError(RuntimeError):
    pass


def _expected(cfg: ProxyConfig) -> list[str]:
    return [m.model_name for m in cfg.model_list]


async def apply_config(config_path: str, reloader) -> dict:
    """Restart the proxy onto the staged config.yaml and verify. On failure, restore
    the last-applied baseline and restart back onto it. On success, promote the
    current config to the baseline."""
    seed_baseline_if_missing(config_path)
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        raise ApplyError(f"config invalid, not applied: {e}") from e
    try:
        await reloader.reload_and_verify(_expected(cfg))
        promote_baseline(config_path)
        return {"applied": True, "models": _expected(cfg),
                "routing_strategy": cfg.router_settings.routing_strategy}
    except ReloadError as e:
        restore_baseline(config_path)
        try:
            await reloader.reload_and_verify(_expected(load_config(config_path)))
        except Exception:
            pass
        raise ApplyError(f"reload failed; rolled back to last applied config: {e}") from e
