from __future__ import annotations

import yaml
from pathlib import Path

from app.config_render import effective, render_config
from app.config_store import validate_config, ConfigError, write_config_atomic
from app.reloader import ReloadError


class ApplyError(RuntimeError):
    pass


def _expected_models(cfg: dict) -> list[str]:
    """Extract model names from a rendered config dict."""
    return [m.get("model_name") for m in (cfg.get("model_list") or [])]


async def apply_config(config_path, store, reloader, *, decrypt) -> dict:
    """Commit-at-write apply flow.

    Pre-commit (failure raises ApplyError, nothing folded):
      1. Render effective config from applied + staged.
      2. validate_config — reject invalid config before touching disk.
      3. Atomic write + read-back + re-parse.

    Commit:
      4. store.fold() — staged→applied, clear staged. This is the commit boundary.

    Post-commit (no rollback, reported via servant status):
      5. reloader.reload_and_verify(expected_models).
         Success → servant="healthy"; ReloadError → servant="unhealthy", applied still True.
    """
    # 1. Render effective config
    eff = effective(await store.applied(), await store.staged())
    cfg = render_config(eff, decrypt)

    # 2. Validate (pre-commit — raise ApplyError on failure, nothing written/folded)
    try:
        validate_config(cfg)
    except ConfigError as e:
        raise ApplyError(f"invalid config, not applied: {e}") from e

    # 3. Atomic write + read-back + re-parse (pre-commit — raise ApplyError on failure)
    text = yaml.safe_dump(cfg, sort_keys=False)
    try:
        write_config_atomic(config_path, text)
        readback = yaml.safe_load(Path(config_path).read_text())
        assert readback is not None, "read-back returned None"
    except Exception as e:
        raise ApplyError(f"write/readback failed, not applied: {e}") from e

    # 4. COMMIT: fold staged into applied, clear staged
    await store.fold()

    # 5. Restart + verify (post-commit — reported, NOT rolled back)
    expected = _expected_models(cfg)
    try:
        await reloader.reload_and_verify(expected)
        return {"applied": True, "servant": "healthy", "models": expected}
    except ReloadError as e:
        return {"applied": True, "servant": "unhealthy", "detail": str(e), "models": expected}


async def pending_status(store) -> dict:
    """Return {pending: bool, count: int} from store's staged count."""
    n = await store.staged_count()
    return {"pending": n > 0, "count": n}
