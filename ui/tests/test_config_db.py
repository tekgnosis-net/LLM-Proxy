"""Tests for ConfigStore (config_db.py) — requires a real PostgreSQL instance.

Set TEST_DATABASE_URL to point at a test DB.  The fixture recreates the schema
tables before each test and drops them after, so tests are isolated.
"""
from __future__ import annotations
import os
import pytest
import asyncpg

from app.config_db import ConfigStore, APPLIED, STAGED

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://test:testpass@localhost:15432/testdb",
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store():
    """Real ConfigStore backed by a scratch Postgres schema.

    Creates the tables fresh, yields a ConfigStore, then drops the tables so
    every test starts with an empty slate.
    """
    try:
        conn = await asyncpg.connect(TEST_DSN)
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"no test Postgres reachable at {TEST_DSN} ({e}); "
                    f"set TEST_DATABASE_URL to run ConfigStore DB tests")
    # Drop tables if they exist from a previous failed run
    await conn.execute(f"DROP TABLE IF EXISTS {STAGED}")
    await conn.execute(f"DROP TABLE IF EXISTS {APPLIED}")
    await conn.close()

    cs = ConfigStore(TEST_DSN)
    # Ensure schema is created
    conn2 = await asyncpg.connect(TEST_DSN)
    await cs.ensure_schema(conn2)
    await conn2.close()

    yield cs

    # Teardown: drop tables
    conn3 = await asyncpg.connect(TEST_DSN)
    await conn3.execute(f"DROP TABLE IF EXISTS {STAGED}")
    await conn3.execute(f"DROP TABLE IF EXISTS {APPLIED}")
    await conn3.close()


# ---------------------------------------------------------------------------
# Task 2: migrate_model_identities
# ---------------------------------------------------------------------------

async def test_migrate_rekeys_legacy_model_items(store):
    # legacy v3.3 shape: name == model_name, data has NO model_name key
    conn = await store._conn()
    await store.ensure_schema(conn)
    await conn.execute("INSERT INTO ui_config_applied(kind,name,data) VALUES('model','gpt-4o',$1)",
                       '{"litellm_params": {"model": "openai/gpt-4o"}}')
    await conn.close()
    await store.migrate_model_identities()
    applied = await store.applied()
    models = [i for i in applied if i["kind"] == "model"]
    assert len(models) == 1
    import uuid; uuid.UUID(models[0]["name"])            # rekeyed to a uuid
    assert models[0]["data"]["model_name"] == "gpt-4o"   # model_name moved into data
    # idempotent: second run is a no-op
    await store.migrate_model_identities()
    assert len([i for i in (await store.applied()) if i["kind"] == "model"]) == 1


# ---------------------------------------------------------------------------
# Task 6: replace_applied
# ---------------------------------------------------------------------------

async def test_replace_applied_swaps_master_and_clears_staged(store):
    await store.stage("model", "old", {"model_name": "o"})
    await store.fold()
    await store.stage("model", "pending", {"model_name": "p"})
    await store.replace_applied([{"kind": "model", "name": "new", "data": {"model_name": "n"}}])
    applied = await store.applied()
    assert [(i["kind"], i["name"]) for i in applied] == [("model", "new")]
    assert await store.staged() == []
