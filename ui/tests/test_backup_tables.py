from app.backup_tables import classify, USAGE_EXACT, NEVER_RESTORE, WATERMARK_COLUMNS


def test_classify_splits_usage_by_exact_and_prefix():
    tables = ["LiteLLM_SpendLogs", "LiteLLM_DailyTagSpend", "LiteLLM_DailyToolSpend",
              "LiteLLM_VerificationToken", "ui_config_applied", "_prisma_migrations",
              "LiteLLM_HealthCheckTable", "LiteLLM_ErrorLogs", "LiteLLM_AuditLog"]
    c = classify(tables)
    assert c["usage"] == sorted(["LiteLLM_SpendLogs", "LiteLLM_DailyTagSpend",
                                 "LiteLLM_DailyToolSpend", "LiteLLM_ErrorLogs", "LiteLLM_AuditLog"])
    assert c["transient"] == ["LiteLLM_HealthCheckTable"]
    assert "_prisma_migrations" in c["config"] and "ui_config_applied" in c["config"]
    assert "LiteLLM_SpendLogs" not in c["config"]


def test_new_daily_table_is_usage_without_code_change():
    c = classify(["LiteLLM_DailyFutureThingSpend", "LiteLLM_TeamTable"])
    assert c["usage"] == ["LiteLLM_DailyFutureThingSpend"]


def test_constants_shape():
    assert "LiteLLM_SpendLogs" in USAGE_EXACT
    assert NEVER_RESTORE == {"_prisma_migrations"}
    assert WATERMARK_COLUMNS["LiteLLM_SpendLogs"] == "startTime"
