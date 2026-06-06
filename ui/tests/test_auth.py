from app.auth import hash_password, verify_password


def test_hash_then_verify_roundtrip():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h) is True


def test_wrong_password_fails():
    h = hash_password("s3cret")
    assert verify_password("nope", h) is False


def test_empty_hash_always_fails():
    assert verify_password("anything", "") is False
