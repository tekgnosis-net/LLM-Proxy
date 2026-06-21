from app.model_content import normalized_managed, content_diff, MANAGED_MODEL_INFO


def test_managed_allowlist_contains_disable_flag():
    assert "disable_background_health_check" in MANAGED_MODEL_INFO


def test_normalized_managed_default_and_bool():
    assert normalized_managed({}) == {"disable_background_health_check": False}
    assert normalized_managed(None) == {"disable_background_health_check": False}
    assert normalized_managed({"disable_background_health_check": None}) == {"disable_background_health_check": False}
    assert normalized_managed({"disable_background_health_check": True}) == {"disable_background_health_check": True}


def test_content_diff_detects_true_vs_absent():
    assert content_diff({"disable_background_health_check": True}, {}) == ["disable_background_health_check"]


def test_content_diff_absent_equals_false_no_drift():
    assert content_diff({}, {"disable_background_health_check": False}) == []
    assert content_diff({}, {}) == []


def test_content_diff_ignores_unmanaged_fields():
    # litellm-derived fields differ but are not managed → no drift
    assert content_diff({"id": "x", "created_at": "t1"},
                        {"id": "x", "created_at": "t2", "db_model": True}) == []
