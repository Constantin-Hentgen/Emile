"""Sync a household's shopping list to its CalDAV calendar (event mode).

One event holds the list: the title says how many items and what they should cost, the description is the
aisle-grouped checklist (✅ bought, ☐ to buy) and the recipes. The household's local state is the source of truth:
what Emile writes is read back before anything is deleted, and only events Emile recognises by their UID suffix
are ever replaced or removed, never the household's own entries.
"""

import datetime
import hashlib
import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select

from emile.core import Kitchen, ShoppingList, ical, render, shopping
from emile.service import repository
from emile.service.db import Database
from emile.service.remote import CalendarRemote, RemoteError, RemoteEvent, RemoteFactory
from emile.service.repository import HouseholdInfo
from emile.service.tables import CalendarConfig
from emile.service.vault import Vault, VaultError

logger = logging.getLogger(__name__)

CLOSE_ENOUGH = datetime.timedelta(seconds=60)

_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)
"""One sync at a time per household (the background loop and ``POST /v1/sync`` must not race: two creations would
delete each other). Emile runs as one process, so a process-local lock is enough."""


class CalendarUnavailable(Exception):
    """Calendar sync needs ``EMILE_SECRET_KEY`` to store the calendar password."""


class NotConfigured(Exception):
    """The household has no calendar."""


class SyncError(Exception):
    """The calendar server refused or failed (the household's data is untouched)."""


@dataclass(frozen=True)
class SyncOutcome:
    status: Literal["synced", "unchanged", "skipped"]
    detail: str | None = None
    event_uid: str | None = None
    removed: int = 0


@dataclass(frozen=True)
class Desired:
    """What the calendar should hold for the current list."""

    uid: str
    summary: str
    description: str
    start: datetime.datetime
    end: datetime.datetime

    @property
    def digest(self) -> str:
        payload = [self.uid, self.summary, self.description, self.start.isoformat(), self.end.isoformat()]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def desired_event(lst: ShoppingList | None, info: HouseholdInfo, now: datetime.datetime) -> Desired | str:
    """The event to write, or the reason there is none (no list, no slot, slot over)."""
    if lst is None or not lst.items:
        return "no shopping list"
    if lst.event_start is None or lst.event_end is None:
        return "the list has no shopping slot (create it with start_time)"
    if lst.event_end < now:
        return "the shopping slot is over"
    lang: render.Lang = "en" if info.language == "en" else "fr"
    return Desired(
        uid=lst.event_uid or ical.new_uid(),
        summary=render.event_summary(lst, lang, info.currency),
        description=render.event_description(lst, lang, info.currency),
        start=lst.event_start,
        end=lst.event_end,
    )


def _same(remote: RemoteEvent, want: Desired) -> bool:
    return (
        remote.summary == want.summary
        and remote.description.replace("\r\n", "\n").strip() == want.description.strip()
        and abs(remote.start - want.start) < CLOSE_ENOUGH
        and abs(remote.end - want.end) < CLOSE_ENOUGH
    )


def push(remote: CalendarRemote, want: Desired, now: datetime.datetime) -> int:
    """Create or update the event, read it back, then remove other upcoming shopping events. Returns how many."""
    existing = remote.get(want.uid)
    if existing is None or not _same(existing, want):
        sequence = existing.sequence + 1 if existing else 0
        remote.put(
            want.uid,
            ical.build_event(
                want.uid, want.summary, want.description, want.start, want.end, now=now, sequence=sequence
            ),
        )
        written = remote.get(want.uid)
        if written is None or not _same(written, want):
            raise RemoteError("read-back: the calendar does not hold the event that was just written")
    removed = 0
    for other in remote.shopping_events():  # only Emile's (or its predecessor's) events: never the user's own
        if other.uid != want.uid and other.end > now:  # past ones stay: they are the history
            remote.delete(other.uid)
            removed += 1
    return removed


def _config(db: Database, household_id: int) -> CalendarConfig:
    with db.session() as session:
        config = session.get(CalendarConfig, household_id)
        if config is None:
            raise NotConfigured("this household has no calendar: configure one with PUT /v1/calendar")
        session.expunge(config)
        return config


def _record(db: Database, household_id: int, *, now: datetime.datetime, digest: str | None, error: str | None) -> None:
    with db.session() as session:
        config = session.get(CalendarConfig, household_id)
        if config is None:
            return
        config.last_error = error
        if error is None:
            config.last_sync_at = now
            config.synced_digest = digest
        else:
            config.synced_digest = None  # whatever was half done gets checked again next time


def sync_household(
    db: Database,
    vault: Vault,
    factory: RemoteFactory,
    household_id: int,
    now: datetime.datetime,
    *,
    force: bool = False,
) -> SyncOutcome:
    """Bring the calendar in line with the current list. Idempotent; ``force`` re-checks the calendar even when
    nothing changed locally (to repair an event someone deleted there)."""
    with _locks[household_id]:
        return _sync(db, vault, factory, household_id, now, force)


def _sync(
    db: Database, vault: Vault, factory: RemoteFactory, household_id: int, now: datetime.datetime, force: bool
) -> SyncOutcome:
    config = _config(db, household_id)
    snapshot: tuple[ShoppingList | None, HouseholdInfo] = repository.read(
        db, household_id, lambda kitchen, info: (kitchen.current_list, info)
    )
    lst, info = snapshot
    want = desired_event(lst, info, now)
    if lst is None or isinstance(want, str):
        return SyncOutcome("skipped", want if isinstance(want, str) else "no shopping list")
    if not force and config.synced_digest == want.digest:
        return SyncOutcome("unchanged", event_uid=want.uid)
    try:
        remote = factory(config.url, config.username, vault.decrypt(config.password_encrypted))
        removed = push(remote, want, now)
    except (RemoteError, VaultError) as e:
        _record(db, household_id, now=now, digest=None, error=str(e))
        logger.warning("household %s: calendar sync failed: %s", household_id, e)
        raise SyncError(str(e)) from None

    def attach(kitchen: Kitchen, info: HouseholdInfo) -> bool:
        current = kitchen.current_list
        if current is None or current.id != lst.id:
            return False  # the list changed while we were syncing: the next sync will catch up
        shopping.attach_event(kitchen, current.id, want.uid, want.start, want.end)
        return True

    if repository.mutate(db, household_id, attach):
        _record(db, household_id, now=now, digest=want.digest, error=None)
        return SyncOutcome("synced", event_uid=want.uid, removed=removed)
    _record(db, household_id, now=now, digest=None, error=None)
    return SyncOutcome("synced", "the list changed during the sync: it will be synced again", want.uid, removed)


def sync_all(
    db: Database, vault: Vault, factory: RemoteFactory, now: datetime.datetime
) -> dict[int, SyncOutcome | str]:
    """The background pass: every household with a calendar; one failing never stops the others."""
    with db.session() as session:
        ids = list(session.scalars(select(CalendarConfig.household_id).order_by(CalendarConfig.household_id)))
    results: dict[int, SyncOutcome | str] = {}
    for household_id in ids:
        try:
            results[household_id] = sync_household(db, vault, factory, household_id, now)
        except (SyncError, NotConfigured) as e:
            results[household_id] = str(e)
    return results
