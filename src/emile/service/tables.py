"""Database tables.

A household's kitchen is stored normalised (one row per list item and per stock entry) so it stays queryable,
but it is always read and written as one aggregate (:mod:`emile.service.repository`): the rules in
:mod:`emile.core` work on a whole :class:`~emile.core.Kitchen`, and the household's ``version`` column makes each
write conditional on nobody else having written in between.
"""

import datetime

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from emile.service.db import Base, UTCDateTime


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime)


class ApiKey(Base):
    """An API key. Only the SHA-256 of the secret is stored; ``prefix`` lets a person recognise a key in a list."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime)


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    timezone: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(8))
    meal_boxes: Mapped[int] = mapped_column(Integer, default=0)
    next_list_id: Mapped[int] = mapped_column(Integer, default=1)
    next_stock_id: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=0)
    """Bumped on every kitchen write; a write expecting another version is retried (optimistic locking)."""
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime)


class Membership(Base):
    __tablename__ = "memberships"

    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime)


class ShoppingListRow(Base):
    """The household's current list (at most one; ids keep counting in ``households.next_list_id``)."""

    __tablename__ = "shopping_lists"

    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    list_id: Mapped[int] = mapped_column(Integer)
    days: Mapped[int | None] = mapped_column(Integer)
    coverage: Mapped[str | None] = mapped_column(String(200))
    shop: Mapped[str | None] = mapped_column(String(60))
    cost_min: Mapped[float | None] = mapped_column(Float)
    cost_max: Mapped[float | None] = mapped_column(Float)
    recipes: Mapped[str | None] = mapped_column(Text)
    recipe_meals: Mapped[list[int]] = mapped_column(JSON, default=list)
    event_uid: Mapped[str | None] = mapped_column(String(255))
    event_start: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime)
    event_end: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime)


class ListItemRow(Base):
    __tablename__ = "list_items"

    household_id: Mapped[int] = mapped_column(
        ForeignKey("shopping_lists.household_id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    qty: Mapped[str | None] = mapped_column(String(40))
    aisle: Mapped[str | None] = mapped_column(String(60))
    keeps: Mapped[int | None] = mapped_column(Integer)
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class StockRow(Base):
    __tablename__ = "stock_entries"

    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    entry_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    qty: Mapped[str | None] = mapped_column(String(40))
    added: Mapped[datetime.date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(10))
    list_id: Mapped[int | None] = mapped_column(Integer)
    list_index: Mapped[int | None] = mapped_column(Integer)
    keeps: Mapped[int | None] = mapped_column(Integer)
    due: Mapped[datetime.date | None] = mapped_column(Date)
    asked: Mapped[datetime.date | None] = mapped_column(Date)
    boxes: Mapped[bool] = mapped_column(Boolean, default=False)


class AisleRank(Base):
    """How early an aisle is started in a shop (0 = first, 1 = last), smoothed over the last trips."""

    __tablename__ = "aisle_ranks"

    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    shop: Mapped[str] = mapped_column(String(60), primary_key=True)
    aisle: Mapped[str] = mapped_column(String(60), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    seen: Mapped[int] = mapped_column(Integer)


class ShopLink(Base):
    """A revocable secret that opens the shop page: it can read the household's current list and tick items, and
    nothing else. Like API keys, only the SHA-256 of the secret is stored."""

    __tablename__ = "shop_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime)


class CalendarConfig(Base):
    """A household's CalDAV calendar. The password is encrypted with ``EMILE_SECRET_KEY``."""

    __tablename__ = "calendar_configs"

    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    url: Mapped[str] = mapped_column(String(500))
    username: Mapped[str] = mapped_column(String(255))
    password_encrypted: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(10), default="event")
    synced_digest: Mapped[str | None] = mapped_column(String(64))
    """Fingerprint of what was last written to the calendar; the sync is skipped while nothing changed."""
    synced_state: Mapped[str | None] = mapped_column(Text)
    """Tasks mode: JSON of each task's state at the last sync (the base of the two-way merge)."""
    last_sync_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime)
