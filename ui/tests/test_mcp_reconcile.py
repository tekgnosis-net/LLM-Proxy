import pytest
from app.mcp_reconcile import build_desired, diff_mcp, reconcile_mcp, mcp_content_diff

DEC = lambda b: b[4:] if b.startswith("ENC:") else b

def _item(name, **over):
    data = {"server_name": "s-" + name, "transport": "http", "url": f"http://h/{name}/mcp",
            "auth_type": None, "static_headers": {}, "extra_headers": [], "allowed_tools": [],
            "allow_all_keys": False, "mcp_info": {}}
    data.update(over)
    return {"kind": "mcp_server", "name": name, "data": data}


def test_build_desired_payload_and_credentials():
    desired, failed = build_desired(
        [_item("u1"), _item("u2", auth_type="bearer_token", auth_value_encrypted="ENC:tok")], DEC)
    assert failed == []
    assert desired["u1"]["server_id"] == "u1" and desired["u1"]["server_name"] == "s-u1"
    assert "credentials" not in desired["u1"]
    assert desired["u2"]["credentials"] == {"auth_value": "tok"}
    assert desired["u2"]["auth_type"] == "bearer_token"


def test_build_desired_presence_only_skips_decrypt():
    desired, failed = build_desired([_item("u2", auth_type="bearer_token", auth_value_encrypted="ENC:tok")], None)
    assert failed == [] and "credentials" not in desired["u2"]


def test_build_desired_decrypt_failure_reported():
    def boom(_): raise ValueError("bad token")
    desired, failed = build_desired([_item("u1", auth_type="api_key", auth_value_encrypted="x")], boom)
    assert desired == {} and failed[0]["op"] == "decrypt"
    assert failed[0]["name"] == "s-u1"


def test_diff_mcp_add_update_delete():
    desired, _ = build_desired([_item("a"), _item("b")], DEC)
    live = [{"server_id": "b"}, {"server_id": "c"}]
    plan = diff_mcp(desired, live, changed_ids={"b"})
    assert [e["server_id"] for e in plan["to_add"]] == ["a"]
    assert [e["server_id"] for e in plan["to_update"]] == ["b"]
    assert plan["to_delete"] == ["c"]


class FakeClient:
    def __init__(self, fail_add=None, team_update_fails=False):
        self.added, self.updated, self.deleted = [], [], []
        self.team_updates, self.team_creates = [], []
        self._fail_add = fail_add or set()
        self._team_update_fails = team_update_fails
    async def add_server(self, p):
        if p["server_id"] in self._fail_add:
            raise RuntimeError("already exists")
        self.added.append(p["server_id"])
    async def update_server(self, p): self.updated.append(p["server_id"])
    async def delete_server(self, sid): self.deleted.append(sid)
    async def update_team(self, p):
        if self._team_update_fails:
            raise RuntimeError("team not found")
        self.team_updates.append(p)
    async def new_team(self, p): self.team_creates.append(p)


@pytest.mark.asyncio
async def test_reconcile_mcp_converges():
    items = [_item("a"), _item("b")]
    live = [{"server_id": "b"}, {"server_id": "gone"}]
    c = FakeClient()
    rep = await reconcile_mcp(items, live, c, changed_item_names={"b"}, decrypt=DEC)
    assert c.added == ["a"] and c.updated == ["b"] and c.deleted == ["gone"]
    assert rep == {"added": 1, "updated": 1, "deleted": 1, "failed": [], "team": "synced"}
    assert c.team_updates[-1] == {"team_id": "ui-mcp",
                                  "object_permission": {"mcp_servers": ["a", "b"]}}


@pytest.mark.asyncio
async def test_reconcile_creates_team_when_update_fails():
    c = FakeClient(team_update_fails=True)
    rep = await reconcile_mcp([_item("a")], [], c, changed_item_names=set(), decrypt=DEC)
    assert rep["team"] == "created"
    assert c.team_creates[-1]["team_id"] == "ui-mcp"


@pytest.mark.asyncio
async def test_reconcile_skips_team_when_no_servers_and_no_team():
    c = FakeClient(team_update_fails=True)
    rep = await reconcile_mcp([], [], c, changed_item_names=set(), decrypt=DEC)
    assert rep["team"] == "skipped" and c.team_creates == []


@pytest.mark.asyncio
async def test_reconcile_add_collision_becomes_update():
    c = FakeClient(fail_add={"a"})
    rep = await reconcile_mcp([_item("a")], [], c, changed_item_names=set(), decrypt=DEC)
    assert c.updated == ["a"] and rep["updated"] == 1 and rep["failed"] == []


def test_content_diff_normalizes_empty():
    desired, _ = build_desired([_item("a")], None)
    live = {"server_id": "a", "server_name": "s-a", "transport": "http", "url": "http://h/a/mcp",
            "auth_type": None, "static_headers": None, "extra_headers": None, "allowed_tools": None,
            "allow_all_keys": False, "mcp_info": None, "status": "healthy"}
    assert mcp_content_diff(desired["a"], live) == []
    live2 = dict(live, url="http://other/mcp")
    assert mcp_content_diff(desired["a"], live2) == ["url"]


def test_content_diff_ignores_mcp_info_decoration():
    # LiteLLM auto-fills mcp_info.server_name/description on the live side
    # (Task 1 report (a)) — only mcp_server_cost_info is ours to compare.
    desired, _ = build_desired([_item("a")], None)
    live = {"server_id": "a", "server_name": "s-a", "transport": "http", "url": "http://h/a/mcp",
            "mcp_info": {"server_name": "s-a", "description": None}, "allow_all_keys": False}
    assert mcp_content_diff(desired["a"], live) == []


# Final-review fix I1: decrypt failure must not delete the live server

@pytest.mark.asyncio
async def test_reconcile_decrypt_failed_but_live_is_protected_from_delete():
    def boom(v):
        if v == "BOOM": raise ValueError("bad token")
        return DEC(v)
    items = [_item("a"), _item("bad", auth_type="api_key", auth_value_encrypted="BOOM")]
    live = [{"server_id": "a"}, {"server_id": "bad"}]
    c = FakeClient()
    rep = await reconcile_mcp(items, live, c, changed_item_names=set(), decrypt=boom)
    assert "bad" not in c.deleted
    assert any(f["id"] == "bad" and f["op"] == "decrypt" for f in rep["failed"])
    assert "bad" in c.team_updates[-1]["object_permission"]["mcp_servers"]


@pytest.mark.asyncio
async def test_reconcile_decrypt_failed_and_not_live_is_not_protected():
    def boom(v):
        if v == "BOOM": raise ValueError("bad token")
        return DEC(v)
    items = [_item("a"), _item("bad", auth_type="api_key", auth_value_encrypted="BOOM")]
    live = [{"server_id": "a"}]
    c = FakeClient()
    rep = await reconcile_mcp(items, live, c, changed_item_names=set(), decrypt=boom)
    assert "bad" not in c.added and "bad" not in c.deleted
    assert "bad" not in c.team_updates[-1]["object_permission"]["mcp_servers"]


def test_diff_mcp_protected_ids_kept_off_to_delete():
    desired, _ = build_desired([_item("a")], DEC)
    live = [{"server_id": "a"}, {"server_id": "b"}, {"server_id": "c"}]
    plan = diff_mcp(desired, live, changed_ids=set(), protected_ids={"b"})
    assert plan["to_delete"] == ["c"]
