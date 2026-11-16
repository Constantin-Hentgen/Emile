"""CalDAV calendar of the household, and the sync that keeps the shopping list in it."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from emile.schemas import CalendarOut, CalendarSet, SyncResult
from emile.service import sync
from emile.service.db import Database
from emile.service.deps import DbDep, HouseholdDep, LimitsDep, NowDep
from emile.service.remote import RemoteFactory
from emile.service.tables import CalendarConfig
from emile.service.vault import Vault

router = APIRouter(tags=["calendar"])


def get_vault(request: Request) -> Vault:
    vault: Vault | None = request.app.state.vault
    if vault is None:
        raise sync.CalendarUnavailable("calendar sync is off: set EMILE_SECRET_KEY to store the calendar password")
    return vault


def get_factory(request: Request) -> RemoteFactory:
    factory: RemoteFactory = request.app.state.remote_factory
    return factory


VaultDep = Annotated[Vault, Depends(get_vault)]
FactoryDep = Annotated[RemoteFactory, Depends(get_factory)]


def _out(config: CalendarConfig) -> CalendarOut:
    return CalendarOut(
        url=config.url,
        username=config.username,
        mode=config.mode,
        last_sync_at=config.last_sync_at,
        last_error=config.last_error,
    )


def _get(db: Database, household_id: int) -> CalendarConfig:
    with db.session() as session:
        config = session.get(CalendarConfig, household_id)
        if config is None:
            raise sync.NotConfigured("this household has no calendar: configure one with PUT /v1/calendar")
        session.expunge(config)
        return config


@router.put("/v1/calendar")
def set_calendar(
    body: CalendarSet, db: DbDep, household: HouseholdDep, now: NowDep, vault: VaultDep, factory: FactoryDep
) -> CalendarOut:
    """Set (or replace) the household's calendar. The connection is tried first: a wrong URL or password is
    refused with 502 `calendar_error`. The password is stored encrypted and never returned.

    Needs `EMILE_SECRET_KEY` (503 `calendar_unavailable` otherwise). Point it at a calendar collection of its own
    (e.g. "Shopping"): Emile only touches events it recognises by their UID, but a dedicated calendar keeps the
    household's other entries out of reach altogether.
    """
    factory(body.url, body.username, body.password).check()
    with db.session() as session:
        config = session.get(CalendarConfig, household.id)
        if config is None:
            config = CalendarConfig(household_id=household.id, created_at=now)
            session.add(config)
        config.url, config.username, config.mode = body.url, body.username, body.mode
        config.password_encrypted = vault.encrypt(body.password)
        config.synced_digest = None
        config.synced_state = None
        config.last_error = None
        session.flush()
        return _out(config)


@router.get("/v1/calendar")
def get_calendar(db: DbDep, household: HouseholdDep) -> CalendarOut:
    """The calendar settings and the state of the last sync (404 if none is set)."""
    return _out(_get(db, household.id))


@router.delete("/v1/calendar", status_code=204)
def delete_calendar(db: DbDep, household: HouseholdDep) -> Response:
    """Stop syncing. Events already in the calendar are left as they are."""
    with db.session() as session:
        config = session.get(CalendarConfig, household.id)
        if config is None:
            raise sync.NotConfigured("this household has no calendar")
        session.delete(config)
    return Response(status_code=204)


@router.post("/v1/sync")
def sync_now(
    db: DbDep, household: HouseholdDep, now: NowDep, vault: VaultDep, factory: FactoryDep, limits: LimitsDep
) -> SyncResult:
    """Sync the shopping list to the calendar now, re-checking the calendar even if nothing changed here.

    The background loop does the same every `EMILE_SYNC_INTERVAL` seconds, so calling this is only needed to see
    the result at once (or when the loop is off). 502 `calendar_error` if the calendar server fails.
    """
    outcome = sync.sync_household(db, vault, factory, household.id, now, force=True, limits=limits)
    return SyncResult(**outcome.__dict__)
