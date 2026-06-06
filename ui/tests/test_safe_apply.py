import pytest
from pathlib import Path
from app.config_store import load_config
from app.safe_apply import safe_apply, SafeApplyError


GOOD = {
    "general_settings": {"store_model_in_db": False},
    "router_settings": {"routing_strategy": "simple-shuffle"},
    "model_list": [{"model_name": "cheap", "litellm_params": {"model": "openai/gpt-4o-mini"}}],
}


class FakeReloader:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0
    async def reload_and_verify(self, expected_models):
        self.calls += 1
        if not self.ok:
            from app.reloader import ReloadError
            raise ReloadError("simulated failure")
        return True


@pytest.mark.asyncio
async def test_safe_apply_writes_and_reloads(tmp_path):
    path = str(tmp_path / "config.yaml")
    Path(path).write_text("router_settings:\n  routing_strategy: least-busy\n")
    rl = FakeReloader(ok=True)
    await safe_apply(path, GOOD, rl)
    assert load_config(path).router_settings.routing_strategy == "simple-shuffle"


@pytest.mark.asyncio
async def test_safe_apply_rejects_invalid_before_write(tmp_path):
    path = str(tmp_path / "config.yaml")
    Path(path).write_text("router_settings:\n  routing_strategy: least-busy\n")
    rl = FakeReloader(ok=True)
    with pytest.raises(SafeApplyError):
        await safe_apply(path, {"router_settings": {"routing_strategy": "lowest-cost"}}, rl)
    assert "least-busy" in Path(path).read_text()
    assert rl.calls == 0


@pytest.mark.asyncio
async def test_safe_apply_rolls_back_on_reload_failure(tmp_path):
    path = str(tmp_path / "config.yaml")
    Path(path).write_text("router_settings:\n  routing_strategy: least-busy\n")
    rl = FakeReloader(ok=False)
    with pytest.raises(SafeApplyError):
        await safe_apply(path, GOOD, rl)
    assert load_config(path).router_settings.routing_strategy == "least-busy"
    assert rl.calls == 2
