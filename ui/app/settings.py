from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    admin_password_hash: str = ""     # argon2 hash
    session_secret: str               # required — no insecure default (signs session cookies)
    session_cookie_secure: bool = False  # set true behind TLS → marks the session cookie Secure (SESSION_COOKIE_SECURE)
    config_path: str = "/config/config.yaml"
    socket_proxy_url: str = "http://socket-proxy:2375"
    litellm_container: str = "litellm-proxy"
    reload_mode: Literal["SIGHUP", "restart"] = "restart"   # spike: this image needs a restart, SIGHUP is a no-op
    reload_timeout_s: float = 90.0    # max wait for the proxy to return healthy after reload
    database_url: str = ""            # asyncpg DSN for housekeeping/stats
    store_model_in_db: bool = False   # mirrors the litellm container's STORE_MODEL_IN_DB; true → hybrid hot-apply
    housekeeping_enabled: bool = False        # opt-in: scheduled maintenance cron
    housekeeping_interval_hours: int = 24
    housekeeping_spendlog_retention_days: int = 90
    housekeeping_delete_expired_keys: bool = True
    redis_host: str = ""    # display only (resolved from compose), e.g. "valkey"
    redis_port: str = ""    # display only, e.g. "6379"
    litellm_proxy_port: str = "4000"   # host-facing proxy port (compose binds it)
    litellm_proxy_host: str = ""       # LAN IP/host to advertise; empty → UI uses location.hostname
    credentials_key: str = ""   # passphrase for the provider-key vault (any string; sha256-derived to a Fernet key); empty → uses session_secret
    catalog_sync_enabled: bool = True
    catalog_sync_interval_days: int = 7
    catalog_pricing_url: str = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    catalog_endpoints_url: str = "https://raw.githubusercontent.com/BerriAI/litellm/main/provider_endpoints_support.json"
    backup_dir: str = "/backups"   # local mount for scheduled backups (BACKUP_DIR)


@lru_cache
def get_settings() -> Settings:
    return Settings()
