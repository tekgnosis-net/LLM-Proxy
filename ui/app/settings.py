from functools import lru_cache

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
    reload_mode: str = "SIGHUP"       # "SIGHUP" or "restart" (set per the Task 4 spike)
    reload_timeout_s: float = 90.0    # max wait for the proxy to return healthy after reload


@lru_cache
def get_settings() -> Settings:
    return Settings()
