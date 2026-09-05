from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TCCS Core"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://tccs:tccs@localhost:5432/tccs"
    management_networks: str = "127.0.0.1/32"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_prefix="TCCS_", env_file=".env", extra="ignore")


settings = Settings()
