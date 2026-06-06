import pytest
from app.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        admin_password_hash="",
        session_secret="test-secret",
        config_path=str(tmp_path / "config.yaml"),
    )
