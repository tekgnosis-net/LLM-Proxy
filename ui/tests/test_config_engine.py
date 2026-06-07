from app.config_db import decide_flag


def test_decide_flag():
    assert decide_flag(applied_has=False) == "new"
    assert decide_flag(applied_has=True) == "changed"
