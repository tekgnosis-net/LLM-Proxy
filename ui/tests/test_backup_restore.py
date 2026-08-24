import json
import pytest
from cryptography.fernet import Fernet
from app.credentials_store import fernet_from_secret
from app.backup_restore import rollback_preview, check_decryptable, parse_export


def _it(kind, name, data): return {"kind": kind, "name": name, "data": data}


def test_rollback_preview_diff():
    cur = [_it("model", "a", {"x": 1}), _it("model", "b", {"x": 1}), _it("router_setting", "timeout", 300)]
    new = [_it("model", "a", {"x": 2}), _it("model", "c", {"x": 1}), _it("router_setting", "timeout", 300)]
    d = rollback_preview(cur, new)
    assert d["added"] == [{"kind": "model", "name": "c"}]
    assert d["removed"] == [{"kind": "model", "name": "b"}]
    assert d["changed"] == [{"kind": "model", "name": "a"}]
    assert d["restart_kinds_changed"] is False
    d2 = rollback_preview(cur, cur[:2] + [_it("router_setting", "timeout", 600)])
    assert d2["restart_kinds_changed"] is True


def test_check_decryptable_flags_wrong_secret():
    f_good, f_bad = fernet_from_secret("good"), fernet_from_secret("bad")
    enc = f_bad.encrypt(b"k").decode()
    items = [_it("credential", "DI", {"provider": "x", "value_encrypted": enc}),
             _it("mcp_server", "m1", {"auth_value_encrypted": enc}),
             _it("model", "a", {})]
    assert check_decryptable(items, f_good) == ["credential/DI", "mcp_server/m1"]
    assert check_decryptable(items, f_bad) == []


def test_parse_export_validates():
    items = [_it("model", "a", {})]
    assert parse_export(json.dumps({"version": 1, "items": items})) == items
    with pytest.raises(ValueError): parse_export("not json")
    with pytest.raises(ValueError): parse_export(json.dumps({"version": 1, "items": "nope"}))
    with pytest.raises(ValueError): parse_export(json.dumps({"version": 1, "items": [{"kind": "model"}]}))
