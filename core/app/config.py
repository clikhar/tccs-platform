from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TCCS Core"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://tccs:tccs@localhost:5432/tccs"
    management_networks: str = "127.0.0.1/32"
    log_level: str = "INFO"
    asterisk_ari_enabled: bool = False
    asterisk_ari_url: str = "http://127.0.0.1:8088/ari"
    asterisk_ari_username: str = "tccs"
    asterisk_ari_password: str = ""
    asterisk_ari_app: str = "tccs-core"
    asterisk_ari_timeout_seconds: float = 10.0
    asterisk_ari_reconnect_delay: float = 2.0

    model_config = SettingsConfigDict(env_prefix="TCCS_", env_file=".env", extra="ignore")


settings = Settings()
