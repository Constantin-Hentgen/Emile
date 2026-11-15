"""Run database migrations from code (app startup, ``emile db upgrade``, tests)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from emile.service.db import Database

MIGRATIONS = Path(__file__).with_name("migrations")


def alembic_config(database: Database) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["url"] = database.url  # via attributes: set_main_option would choke on "%" in passwords
    return config


def upgrade(database: Database, revision: str = "head") -> None:
    """Bring the schema to ``revision``; safe to call on every start (a no-op when up to date)."""
    command.upgrade(alembic_config(database), revision)


def downgrade(database: Database, revision: str) -> None:
    command.downgrade(alembic_config(database), revision)
