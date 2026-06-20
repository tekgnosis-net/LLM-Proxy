import pytest
from app.model_reconcile import diff_models, reconcile_models


def _entry(i): return {"model_name": i, "litellm_params": {"model": "openai/x", "api_key": "sk"}, "model_info": {"id": i}}
def _live(i):  return {"model_name": i, "litellm_params": {"model": "openai/x", "api_key": "**masked**"}, "model_info": {"id": i}}


def test_diff_add_and_delete_by_id():
    desired = {"a": _entry("a"), "b": _entry("b")}
    live = [_live("b"), _live("c")]
    d = diff_models(desired, live, changed_ids=set(), force_ids=set())
    assert [e["model_info"]["id"] for e in d["to_add"]] == ["a"]
    assert d["to_delete"] == ["c"]
    assert d["to_update"] == []           # b in both but not changed/forced → no update


def test_diff_update_only_when_changed_or_forced():
    desired = {"a": _entry("a"), "b": _entry("b")}
    live = [_live("a"), _live("b")]
    d = diff_models(desired, live, changed_ids={"a"}, force_ids={"b"})
    assert sorted(e["model_info"]["id"] for e in d["to_update"]) == ["a", "b"]
    assert d["to_add"] == [] and d["to_delete"] == []


def test_diff_noop():
    desired = {"a": _entry("a")}
    d = diff_models(desired, [_live("a")], changed_ids=set(), force_ids=set())
    assert d == {"to_add": [], "to_update": [], "to_delete": []}


def test_diff_changed_id_not_in_live_is_add_not_update():
    desired = {"a": _entry("a")}
    d = diff_models(desired, [], changed_ids={"a"}, force_ids=set())
    assert [e["model_info"]["id"] for e in d["to_add"]] == ["a"]
    assert d["to_update"] == []   # 'a' is "changed" but absent from live → add, never update


class FakeModelsClient:
    def __init__(self): self.added = []; self.updated = []; self.deleted = []
    async def add_model(self, p): self.added.append(p); return {}
    async def update_model(self, p): self.updated.append(p); return {}
    async def delete_model(self, i): self.deleted.append(i); return {}


def _model_item(name, cred=None):
    lp = {"model": "openai/gpt-4o"}
    if cred: lp["litellm_credential_name"] = cred
    return {"kind": "model", "name": name, "data": {"model_name": name, "litellm_params": lp, "model_info": {}}, "flag": None}


@pytest.mark.asyncio
async def test_reconcile_adds_and_inlines_key():
    client = FakeModelsClient()
    items = [_model_item("a", cred="openai")]
    rep = await reconcile_models(items, live=[], client=client,
                                 changed_ids=set(), force_ids=set(), resolve_key=lambda n: "sk-REAL")
    assert rep["added"] == 1 and rep["failed"] == []
    assert client.added[0]["litellm_params"]["api_key"] == "sk-REAL"
    assert "litellm_credential_name" not in client.added[0]["litellm_params"]


@pytest.mark.asyncio
async def test_reconcile_missing_credential_reported_not_pushed():
    client = FakeModelsClient()
    items = [_model_item("a", cred="ghost")]
    rep = await reconcile_models(items, live=[], client=client,
                                 changed_ids=set(), force_ids=set(), resolve_key=lambda n: None)
    assert client.added == []
    assert rep["added"] == 0
    assert rep["failed"][0]["id"] == "a" and rep["failed"][0]["op"] == "resolve"


@pytest.mark.asyncio
async def test_reconcile_deletes_drifted_live_model():
    client = FakeModelsClient()
    live = [{"model_name": "z", "model_info": {"id": "z"}}]
    rep = await reconcile_models([], live=live, client=client,
                                 changed_ids=set(), force_ids=set(), resolve_key=lambda n: "")
    assert client.deleted == ["z"] and rep["deleted"] == 1
