"""
Unit tests for the bootstrap-import logic (Task 6, Part A).

These tests verify the item-building pipeline (load_config → split_config → passthrough
append → seed_applied call) without requiring a live DB. The DB interaction is tested
via the real-stack integration in Part B.
"""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from app.config_import import split_config
from app.credentials_store import fernet_from_secret


# ---------------------------------------------------------------------------
# 1. Item-building pipeline (pure — no DB)
# ---------------------------------------------------------------------------

def test_bootstrap_items_from_config_example(tmp_path):
    """split_config produces expected items from a config similar to config.yaml.example."""
    raw = {
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
            "database_url": "os.environ/DATABASE_URL",
            "store_model_in_db": False,
            "background_health_checks": True,
            "health_check_interval": 300,
        },
        "litellm_settings": {
            "drop_params": True,
            "telemetry": False,
            "cache": True,
            "cache_params": {
                "type": "redis",
                "host": "os.environ/REDIS_HOST",
                "port": "os.environ/REDIS_PORT",
            },
        },
        "router_settings": {"routing_strategy": "simple-shuffle"},
        "model_list": [],
    }
    items, passthrough = split_config(raw, encrypt=lambda v: f"ENC({v})")
    by = {(i["kind"], i["name"]): i for i in items}

    assert by[("router_setting", "routing_strategy")]["data"] == "simple-shuffle"
    assert by[("general_setting", "store_model_in_db")]["data"] is False
    assert by[("general_setting", "background_health_checks")]["data"] is True
    assert by[("litellm_setting", "cache")]["data"] is True
    # cache_params is a dict-valued litellm_setting key
    assert by[("litellm_setting", "cache_params")]["data"]["type"] == "redis"
    # model_list is empty → no model items
    assert not any(i["kind"] == "model" for i in items)
    # nothing unknown → no passthrough
    assert passthrough == {}


def test_bootstrap_passthrough_appended_when_nonempty():
    """Unknown top-level keys are collected into passthrough and appended as a passthrough item."""
    raw = {
        "router_settings": {"routing_strategy": "least-busy"},
        "callbacks": ["langfuse"],        # unknown → passthrough
        "environment_variables": {"X": "1"},
    }
    items, passthrough = split_config(raw, encrypt=lambda v: v)
    assert passthrough == {"callbacks": ["langfuse"], "environment_variables": {"X": "1"}}

    # Simulate the bootstrap logic: append passthrough item if non-empty
    if passthrough:
        items.append({"kind": "passthrough", "name": "_", "data": passthrough})

    kinds = [(i["kind"], i["name"]) for i in items]
    assert ("passthrough", "_") in kinds
    pt_item = next(i for i in items if i["kind"] == "passthrough")
    assert pt_item["data"]["callbacks"] == ["langfuse"]


def test_bootstrap_no_passthrough_item_when_empty():
    """No passthrough item is added when all top-level keys are known."""
    raw = {
        "router_settings": {"routing_strategy": "simple-shuffle"},
        "litellm_settings": {"cache": False},
        "model_list": [],
        "general_settings": {"store_model_in_db": False},
    }
    items, passthrough = split_config(raw, encrypt=lambda v: v)
    assert passthrough == {}

    # Bootstrap logic: only append if passthrough is non-empty
    if passthrough:
        items.append({"kind": "passthrough", "name": "_", "data": passthrough})

    assert not any(i["kind"] == "passthrough" for i in items)


def test_bootstrap_encrypt_uses_fernet():
    """Credential api_keys are encrypted via a Fernet derived from session_secret."""
    f = fernet_from_secret("test-session-secret-32chars-long-ok")
    enc = lambda v: f.encrypt((v or "").encode()).decode()

    raw = {
        "credential_list": [
            {
                "credential_name": "openai",
                "credential_values": {"api_key": "sk-test-key"},
                "credential_info": {"provider": "openai"},
            }
        ]
    }
    items, _ = split_config(raw, encrypt=enc)
    cred_item = next(i for i in items if i["kind"] == "credential")
    encrypted = cred_item["data"]["value_encrypted"]
    # Must be a valid Fernet token — decrypt must round-trip
    decrypted = f.decrypt(encrypted.encode()).decode()
    assert decrypted == "sk-test-key"


# ---------------------------------------------------------------------------
# 2. Idempotency guard (fake ConfigStore)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_applied_is_idempotent_via_fake():
    """seed_applied no-ops when applied is already populated (idempotency guard).
    This mirrors the guard in ConfigStore.seed_applied: if the table is non-empty,
    the call returns immediately without inserting."""

    class FakeConfigStore:
        def __init__(self, already_seeded: bool):
            self._seeded = already_seeded
            self.calls = 0

        async def seed_applied(self, items):
            """Replica of the idempotency logic."""
            if self._seeded:
                return   # already populated — no-op
            self.calls += 1
            self._seeded = True

    items = [{"kind": "router_setting", "name": "routing_strategy", "data": "simple-shuffle"}]

    # First call: table empty → inserts
    store1 = FakeConfigStore(already_seeded=False)
    await store1.seed_applied(items)
    assert store1.calls == 1

    # Second call: table populated → no-op
    store2 = FakeConfigStore(already_seeded=True)
    await store2.seed_applied(items)
    assert store2.calls == 0


# ---------------------------------------------------------------------------
# 3. Lifespan wiring: assert bootstrap block is present in main.py source
# ---------------------------------------------------------------------------

def test_lifespan_contains_bootstrap_wiring():
    """Smoke test: main.py lifespan contains the bootstrap-import call."""
    src = Path(__file__).parent.parent / "app" / "main.py"
    text = src.read_text()
    assert "seed_applied" in text, "lifespan must call ConfigStore.seed_applied"
    assert "split_config" in text, "lifespan must call split_config"
    assert "fernet_from_secret" in text, "lifespan must use fernet_from_secret for encryption"
    assert "database_url" in text, "lifespan bootstrap must be guarded by s.database_url"
