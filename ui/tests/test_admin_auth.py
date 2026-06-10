import pytest
from fastapi import HTTPException
from app.auth import hash_password, verify_password
from app.admin_auth import verify_and_hash


def test_verify_and_hash_ok():
    eff = hash_password("oldpass123")
    h = verify_and_hash("oldpass123", "newpass456", eff)
    assert verify_password("newpass456", h) and not verify_password("oldpass123", h)


def test_verify_and_hash_wrong_old():
    eff = hash_password("oldpass123")
    with pytest.raises(HTTPException) as e:
        verify_and_hash("WRONG", "newpass456", eff)
    assert e.value.status_code == 401


def test_verify_and_hash_short_new():
    eff = hash_password("oldpass123")
    with pytest.raises(HTTPException) as e:
        verify_and_hash("oldpass123", "short", eff)
    assert e.value.status_code == 422
