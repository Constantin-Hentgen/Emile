"""Fixtures for the HTTP service: a fresh database per test (SQLite file, or Postgres when
EMILE_TEST_DATABASE_URL is set), a frozen clock, and accounts with API keys."""

import datetime
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from emile.service import accounts
from emile.service.app import create_app
from emile.service.settings import Settings

POSTGRES_URL = os.environ.get("EMILE_TEST_DATABASE_URL")
ADMIN_KEY = "test-admin-key-0123456789abcdef"
# 18:00 in Paris on Monday 9 November 2026
NOW = datetime.datetime(2026, 11, 9, 17, 0, tzinfo=datetime.UTC)
TODAY = datetime.date(2026, 11, 9)


class FrozenClock:
    def __init__(self, now: datetime.datetime) -> None:
        self.now = now

    def __call__(self) -> datetime.datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += datetime.timedelta(**delta)


@pytest.fixture
def database_url(tmp_path) -> str:
    if POSTGRES_URL:
        engine = create_engine(POSTGRES_URL)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()
        return POSTGRES_URL
    return f"sqlite:///{tmp_path}/emile.db"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
def settings(database_url) -> Settings:
    return Settings(_env_file=None, database_url=database_url, admin_key=ADMIN_KEY)


@pytest.fixture
def app(settings, clock) -> FastAPI:
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as c:  # runs the lifespan: migrations
        yield c


@dataclass
class Account:
    user_id: int
    household_id: int
    key: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"}


type MakeAccount = Callable[..., Account]


@pytest.fixture
def make_account(app, client) -> MakeAccount:
    def make(
        name: str,
        *,
        household: int | None = None,
        timezone: str = "Europe/Paris",
        language: str = "fr",
        boxes: int = 3,
    ) -> Account:
        with app.state.db.session() as session:
            user = accounts.create_user(session, name, NOW)
            if household is None:
                row = accounts.create_household(
                    session, f"{name}'s", timezone=timezone, language=language, currency="€",
                    meal_boxes=boxes, members=[user], now=NOW,
                )  # fmt: skip
            else:
                row = accounts.find_household(session, household)
                accounts.add_member(session, row, user, NOW)
            __, secret = accounts.create_key(session, user, "test", NOW)
            return Account(user.id, row.id, secret)

    return make


@pytest.fixture
def alice(make_account) -> Account:
    return make_account("alice")


@pytest.fixture
def api(client, alice) -> TestClient:
    """The test client, authenticated as alice."""
    client.headers.update(alice.headers)
    return client
