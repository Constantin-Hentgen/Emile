"""Service configuration, read from ``EMILE_*`` environment variables.

Every variable is prefixed so Emile can share a Portainer stack (and its single variable namespace) with other
services, such as the Bob bot, without clashing on generic names like ``LOG_LEVEL`` or ``DATABASE_URL``.
Each field must be documented in ``.env.example``; a test enforces it.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Defaults suit a local run; the Docker image overrides host and database location."""

    model_config = SettingsConfigDict(env_prefix="EMILE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/emile.db"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Settings are read once per process; tests clear the cache when they change the environment."""
    return Settings()
