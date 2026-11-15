"""Database engine and sessions. SQLite by default, any SQLAlchemy URL (Postgres) by configuration."""

import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Engine, MetaData, create_engine, event, text
from sqlalchemy.engine import Dialect, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

# Explicit constraint names: Alembic can then alter them, and SQLite batch migrations stay deterministic.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class UTCDateTime(TypeDecorator[datetime.datetime]):
    """Timestamps stored in UTC and always returned time-zone aware.

    SQLite has no time-zone type and hands back naive values; normalising here means the rest of the code never
    compares naive and aware datetimes.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime.datetime | None, dialect: Dialect) -> datetime.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass an aware datetime")
        return value.astimezone(datetime.UTC)

    def process_result_value(self, value: datetime.datetime | None, dialect: Dialect) -> datetime.datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value.astimezone(datetime.UTC)


def make_engine(url: str) -> Engine:
    """Create the engine; for SQLite, create the file's folder and set the pragmas a small server needs."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True)
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")  # off by default in SQLite
        cursor.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


class Database:
    """Engine plus session factory, owned by the app (or the CLI) for its lifetime."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = make_engine(url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A transaction: committed on success, rolled back on error."""
        with self._sessions() as session, session.begin():
            yield session

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    def dispose(self) -> None:
        self.engine.dispose()
