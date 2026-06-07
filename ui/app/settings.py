from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    admin_password_hash: str = ""     # argon2 hash
    session_secret: str               # required — no insecure default (signs session cookies)
    config_path: str = "/config/config.yaml"
    socket_proxy_url: str = "http://socket-proxy:2375"
    litellm_container: str = "litellm-proxy"
    reload_mode: Literal["SIGHUP", "restart"] = "restart"   # spike: this image needs a restart, SIGHUP is a no-op
    reload_timeout_s: float = 90.0    # max wait for the proxy to return healthy after reload
    database_url: str = ""            # asyncpg DSN for housekeeping/stats
    housekeeping_enabled: bool = False        # opt-in: scheduled maintenance cron
    housekeeping_interval_hours: int = 24
    housekeeping_spendlog_retention_days: int = 90
    housekeeping_delete_expired_keys: bool = True
    redis_host: str = ""    # display only (resolved from compose), e.g. "valkey"
    redis_port: str = ""    # display only, e.g. "6379"
    credentials_key: str = ""   # Fernet key (urlsafe-b64, 32 bytes); empty → derived from session_secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
