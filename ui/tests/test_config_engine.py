from app.config_db import decide_flag


def test_decide_flag():
    assert decide_flag(applied_has=False) == "new"
    assert decide_flag(applied_has=True) == "changed"


# --- Task 5: apply_config + pending_status (TDD with fakes) ---

import pytest
from app.config_engine import apply_config, ApplyError, pending_status


class FakeStore:
    def __init__(self):
        self._applied = [{"kind": "router_setting", "name": "routing_strategy", "data": "simple-shuffle"}]
        self._staged = [{"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": "changed"}]
        self.folded = False

    async def applied(self):
        return list(self._applied)

    async def staged(self):
        return list(self._staged)

    async def staged_count(self):
        return len(self._staged)

    async def fold(self):
        self.folded = True
        self._staged = []


class FakeReloader:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    async def reload_and_verify(self, expected):
        self.calls += 1
        if not self.ok:
            from app.reloader import ReloadError
            raise ReloadError("sim")
        return True


@pytest.mark.asyncio
async def test_apply_commits_then_folds_then_restarts(tmp_path):
    p = str(tmp_path / "config.yaml")
    store = FakeStore()
    rl = FakeReloader(ok=True)
    res = await apply_config(p, store, rl, decrypt=lambda b: "")
    assert res["applied"] is True
    assert res["servant"] == "healthy"
    assert store.folded is True
    import yaml
    assert yaml.safe_load(open(p))["router_settings"]["routing_strategy"] == "least-busy"


@pytest.mark.asyncio
async def test_apply_servant_unhealthy_still_committed(tmp_path):
    p = str(tmp_path / "config.yaml")
    store = FakeStore()
    rl = FakeReloader(ok=False)
    res = await apply_config(p, store, rl, decrypt=lambda b: "")
    assert res["applied"] is True
    assert res["servant"] == "unhealthy"
    assert store.folded is True   # committed despite restart failure — NO rollback
    import yaml
    assert yaml.safe_load(open(p))["router_settings"]["routing_strategy"] == "least-busy"


@pytest.mark.asyncio
async def test_apply_validate_error_does_not_fold(tmp_path):
    """Pre-commit: bad config → ApplyError, staged intact, nothing written/folded."""
    class BadConfigStore(FakeStore):
        async def staged(self):
            # Return a staged item that would cause validate_config to fail:
            # a literal api_key in a model (rejected by secret-field guardrail)
            return [{"kind": "model", "name": "x",
                     "data": {"litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-LITERAL"}},
                     "flag": "new"}]

    store = BadConfigStore()
    rl = FakeReloader(ok=True)
    p = str(tmp_path / "config.yaml")
    with pytest.raises(ApplyError) as exc_info:
        await apply_config(p, store, rl, decrypt=lambda b: "sk-LITERAL")
    assert "invalid" in str(exc_info.value).lower()
    assert store.folded is False   # pre-commit failure: nothing folded


@pytest.mark.asyncio
async def test_pending_status_with_fake_store():
    store = FakeStore()
    result = await pending_status(store)
    assert result["pending"] is True
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_pending_status_empty():
    class EmptyStore:
        async def staged_count(self):
            return 0

    result = await pending_status(EmptyStore())
    assert result["pending"] is False
    assert result["count"] == 0
