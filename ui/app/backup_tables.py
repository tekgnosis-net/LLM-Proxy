"""Single source of truth for which tables belong to which backup tier (spec §2)."""
from __future__ import annotations

USAGE_EXACT = {"LiteLLM_SpendLogs", "LiteLLM_SpendLogToolIndex",
               "LiteLLM_SpendLogGuardrailIndex", "LiteLLM_ErrorLogs", "LiteLLM_AuditLog"}
USAGE_PREFIXES = ("LiteLLM_Daily",)
TRANSIENT = {"LiteLLM_HealthCheckTable"}          # in neither tier
NEVER_RESTORE = {"_prisma_migrations"}            # dumped for provenance, never truncated/restored

# Logs-tier export strategies (spec §2). A table whose column is missing live is
# skipped with a manifest warning — never a crash (the engine checks columns).
WATERMARK_COLUMNS = {"LiteLLM_SpendLogs": "startTime",
                     "LiteLLM_SpendLogToolIndex": "start_time",
                     "LiteLLM_SpendLogGuardrailIndex": "start_time",
                     "LiteLLM_ErrorLogs": "startTime",
                     "LiteLLM_AuditLog": "updated_at"}
ROLLING_DATE_COLUMN = "date"                      # all LiteLLM_Daily* aggregates
ROLLING_WINDOW_DAYS = 3
WATERMARK_GUARD_S = 60                            # don't export the last 60 s (batch writer race)


def classify(tables: list[str]) -> dict:
    usage = sorted(t for t in tables
                   if t in USAGE_EXACT or any(t.startswith(p) for p in USAGE_PREFIXES))
    transient = sorted(t for t in tables if t in TRANSIENT)
    config = sorted(t for t in tables if t not in set(usage) and t not in TRANSIENT)
    return {"config": config, "usage": usage, "transient": transient}


async def base_tables(conn) -> list[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'")
    return [r["table_name"] for r in rows]
