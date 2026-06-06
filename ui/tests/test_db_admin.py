from app.db_admin import maintenance_sql, RETENTION_TABLE


def test_spendlog_trim_sql_is_parameterized_and_scoped():
    sql = maintenance_sql(retention_days=30)["trim_spend_logs"]
    assert '"LiteLLM_SpendLogs"' in sql
    assert "startTime" in sql
    assert "$1" in sql            # parameterized interval, no string interpolation of user data


def test_expired_keys_sql_targets_verification_token():
    sql = maintenance_sql(retention_days=30)["delete_expired_keys"]
    assert '"LiteLLM_VerificationToken"' in sql and "expires" in sql
