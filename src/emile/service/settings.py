"""Service configuration, read from ``EMILE_*`` environment variables.

Every variable is prefixed so Emile can share a Portainer stack (and its single variable namespace) with other
services, such as the Bob bot, without clashing on generic names like ``LOG_LEVEL`` or ``DATABASE_URL``.
Each field must be documented in ``.env.example``; a test enforces it.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from emile.core.limits import Limits
from emile.schemas import _check_timezone


class Settings(BaseSettings):
    """Runtime settings. Defaults suit a local run; the Docker image overrides host and database location."""

    model_config = SettingsConfigDict(env_prefix="EMILE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/emile.db"
    auto_migrate: bool = True
    """Apply database migrations at startup (turn off to run ``emile db upgrade`` yourself)."""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000

    admin_key: SecretStr | None = None
    """Enables ``/v1/admin``: whoever holds it can create users, keys and households."""

    default_timezone: str = "Europe/Paris"
    default_language: Literal["fr", "en"] = "fr"
    default_currency: str = Field(default="€", min_length=1, max_length=8)

    max_list_items: int = Field(default=60, ge=1, le=1000)
    max_stock_items: int = Field(default=120, ge=1, le=10000)
    max_keeps_days: int = Field(default=60, ge=1, le=3650)
    ask_grace_days: int = Field(default=3, ge=0, le=365)

    _tz = field_validator("default_timezone")(_check_timezone)

    @field_validator("admin_key")
    @classmethod
    def _admin_key_strength(cls, value: SecretStr | None) -> SecretStr | None:
        """An empty variable (as compose passes it) means "disabled"; a short one is refused rather than weak."""
        if value is None or not value.get_secret_value():
            return None
        if len(value.get_secret_value()) < 24:
            raise ValueError("EMILE_ADMIN_KEY must be at least 24 characters (e.g. `openssl rand -hex 32`)")
        return value

    @property
    def limits(self) -> Limits:
        return Limits(
            max_list_items=self.max_list_items,
            max_stock_items=self.max_stock_items,
            max_keeps_days=self.max_keeps_days,
            ask_grace_days=self.ask_grace_days,
        )


@lru_cache
def get_settings() -> Settings:
    """Settings are read once per process; tests clear the cache when they change the environment."""
    return Settings()
