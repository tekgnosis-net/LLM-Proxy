from pathlib import Path

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
async def test_apply_validate_error_does_not_write_or_fold(tmp_path):
    """Pre-commit: validate_config rejects config → ApplyError, nothing written, nothing folded."""
    class BadRoutingStore(FakeStore):
        async def applied(self):
            return []

        async def staged(self):
            # routing_strategy "lowest-cost" is rejected by the guardrail
            return [{"kind": "router_setting", "name": "routing_strategy",
                     "data": "lowest-cost", "flag": "new"}]

    p = str(tmp_path / "config.yaml")
    store = BadRoutingStore()
    rl = FakeReloader(ok=True)
    with pytest.raises(ApplyError) as exc_info:
        await apply_config(p, store, rl, decrypt=lambda b: b)
    assert "invalid" in str(exc_info.value).lower()
    assert store.folded is False          # pre-commit: nothing folded
    assert not Path(p).exists()          # live file was never touched


@pytest.mark.asyncio
async def test_apply_write_failure_does_not_fold(tmp_path, monkeypatch):
    """Pre-commit: write_config_atomic raises → ApplyError wrapping write/readback, nothing folded."""
    import app.config_engine as engine_mod

    def boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(engine_mod, "write_config_atomic", boom)

    p = str(tmp_path / "config.yaml")
    store = FakeStore()
    rl = FakeReloader(ok=True)
    with pytest.raises(ApplyError) as exc_info:
        await apply_config(p, store, rl, decrypt=lambda b: b)
    assert "write/readback" in str(exc_info.value)
    assert store.folded is False          # pre-commit: nothing folded


@pytest.mark.asyncio
async def test_apply_fold_failure_surfaces(tmp_path):
    """Post-write: fold() raises → ApplyError mentioning staging not cleared; file IS written."""
    class FailFoldStore(FakeStore):
        async def fold(self):
            raise RuntimeError("DB connection lost")

    p = str(tmp_path / "config.yaml")
    store = FailFoldStore()
    rl = FakeReloader(ok=True)
    with pytest.raises(ApplyError) as exc_info:
        await apply_config(p, store, rl, decrypt=lambda b: b)
    assert "staging not cleared" in str(exc_info.value)
    # The file WAS written (commit happened at the file level)
    assert Path(p).exists()
    import yaml
    assert yaml.safe_load(open(p)) is not None


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


# --- Task 5: hybrid apply tests ---

from app.config_engine import apply_config as _apply  # already imported above; alias for clarity


class FakeModelsClient:
    def __init__(self): self.added=[]; self.updated=[]; self.deleted=[]
    async def list_models(self): return []
    async def add_model(self, p): self.added.append(p); return {}
    async def update_model(self, p): self.updated.append(p); return {}
    async def delete_model(self, i): self.deleted.append(i); return {}


class ModelStagedStore(FakeStore):
    """One staged NEW model, no settings staged."""
    def __init__(self):
        self._applied = []
        self._staged = [{"kind": "model", "name": "uuid-1",
                         "data": {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {}},
                         "flag": "new"}]
        self.folded = False
    async def fold(self): self.folded = True; self._staged = []


@pytest.mark.asyncio
async def test_hybrid_model_only_no_restart(tmp_path):
    store = ModelStagedStore(); mc = FakeModelsClient(); rl = FakeReloader(ok=True)
    p = str(tmp_path / "config.yaml")
    res = await apply_config(p, store, rl, decrypt=lambda b: "", models_client=mc, hybrid=True)
    assert res["restart"] == "skipped"          # no settings staged → no restart
    assert rl.calls == 0
    assert res["models"]["added"] == 1
    assert mc.added[0]["model_info"]["id"] == "uuid-1"
    assert store.folded is True
    from pathlib import Path
    assert not Path(p).exists()                  # no settings change → no file write


@pytest.mark.asyncio
async def test_hybrid_settings_change_writes_and_restarts(tmp_path):
    store = FakeStore(); mc = FakeModelsClient(); rl = FakeReloader(ok=True)  # FakeStore stages a router_setting
    p = str(tmp_path / "config.yaml")
    res = await apply_config(p, store, rl, decrypt=lambda b: "", models_client=mc, hybrid=True)
    assert res["restart"] == "healthy"
    assert rl.calls == 1
    import yaml
    cfg = yaml.safe_load(open(p))
    assert cfg["router_settings"]["routing_strategy"] == "least-busy"
    assert cfg["model_list"] == []               # hybrid: settings-only, models not in yaml
    assert "credential_list" not in cfg
