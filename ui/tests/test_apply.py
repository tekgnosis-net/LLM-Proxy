"""Legacy v2 app.apply (file-diff rollback) — superseded by app.config_engine in v3; kept until v3.2 removes the v2 config routes."""
import pytest
from pathlib import Path
from app.config_store import write_config, pending_status, load_config
from app.apply import apply_config, ApplyError


class FakeReloader:
    def __init__(self, ok=True): self.ok = ok; self.calls = 0
    async def reload_and_verify(self, expected_models):
        self.calls += 1
        if not self.ok:
            from app.reloader import ReloadError
            raise ReloadError("sim")
        return True


def _cfg(tmp_path, routing):
    p = str(tmp_path / "config.yaml")
    write_config(p, {"router_settings": {"routing_strategy": routing}, "model_list": []})
    return p


@pytest.mark.asyncio
async def test_apply_promotes_baseline_and_clears_pending(tmp_path):
    p = _cfg(tmp_path, "simple-shuffle")
    pending_status(p)
    write_config(p, {"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    assert pending_status(p)["pending"] is True
    await apply_config(p, FakeReloader(ok=True))
    assert pending_status(p)["pending"] is False
    assert load_config(p).router_settings.routing_strategy == "least-busy"


@pytest.mark.asyncio
async def test_apply_rolls_back_on_reload_failure(tmp_path):
    p = _cfg(tmp_path, "simple-shuffle")
    pending_status(p)
    write_config(p, {"router_settings": {"routing_strategy": "least-busy"}, "model_list": []})
    rl = FakeReloader(ok=False)
    with pytest.raises(ApplyError):
        await apply_config(p, rl)
    assert load_config(p).router_settings.routing_strategy == "simple-shuffle"
    assert rl.calls == 2
