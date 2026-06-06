import pytest
from app.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # get_settings() is lru_cached; clear it around each test so env-var-based
    # setup (used by later tasks' app tests) is honored and tests stay isolated.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        admin_password_hash="",
        session_secret="test-secret",
        config_path=str(tmp_path / "config.yaml"),
    )
