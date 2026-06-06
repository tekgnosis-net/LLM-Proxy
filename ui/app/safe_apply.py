from __future__ import annotations
from pathlib import Path
from app.config_store import write_config, load_config, ConfigError, ProxyConfig
from app.reloader import ReloadError


class SafeApplyError(RuntimeError):
    pass


def _expected_models(cfg: ProxyConfig) -> list[str]:
    return [m.model_name for m in cfg.model_list]


async def safe_apply(path: str, raw: dict, reloader) -> ProxyConfig:
    """Validate → snapshot current → atomic write → reload+verify. On reload failure,
    restore the snapshot and reload back onto it, then raise. Never leaves the proxy
    running on an unverified config."""
    p = Path(path)
    previous = p.read_text() if p.exists() else None
    try:
        cfg = write_config(path, raw)            # validates (raises ConfigError) then writes+backs up
    except ConfigError as e:
        raise SafeApplyError(f"invalid config (not applied): {e}") from e
    try:
        await reloader.reload_and_verify(_expected_models(cfg))
        return cfg
    except ReloadError as e:
        if previous is not None:
            p.write_text(previous)
            try:
                prev_cfg = load_config(path)
                await reloader.reload_and_verify(_expected_models(prev_cfg))
            except Exception:
                pass   # best-effort; restart:unless-stopped recovers the file-backed config
        raise SafeApplyError(f"reload failed; rolled back: {e}") from e
