"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Shopify Configuration
    shopify_store_domain: str = ""
    shopify_access_token: str = ""
    shopify_webhook_secret: str = ""

    # TecovaSuite Configuration
    tecovasuite_api_url: str = "https://tecovasuite.tecovas.workers.dev/api/v1"
    tecovasuite_api_key: str = ""

    # Celery Configuration
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # Application Configuration
    log_level: str = "INFO"
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
