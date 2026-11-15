"""Request dependencies: database, clock, the authenticated caller and the household they act on."""

import datetime
import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from emile.core import Limits
from emile.service import accounts
from emile.service.db import Database
from emile.service.repository import HouseholdInfo
from emile.service.settings import Settings
from emile.service.tables import Household

bearer = HTTPBearer(auto_error=False, description="An API key created with `emile keys create` (or the admin API).")

type Clock = Callable[[], datetime.datetime]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_now(request: Request) -> datetime.datetime:
    """Current time, from the app's clock (tests freeze it)."""
    clock: Clock = request.app.state.clock
    return clock()


def get_limits(settings: Annotated[Settings, Depends(get_settings)]) -> Limits:
    return settings.limits


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_caller(
    db: Annotated[Database, Depends(get_db)],
    now: Annotated[datetime.datetime, Depends(get_now)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> accounts.Caller:
    if credentials is None:
        raise _unauthorized("missing API key (Authorization: Bearer …)")
    with db.session() as session:
        caller = accounts.authenticate(session, credentials.credentials, now)
    if caller is None:
        raise _unauthorized("invalid or revoked API key")
    return caller


def get_household(
    db: Annotated[Database, Depends(get_db)],
    caller: Annotated[accounts.Caller, Depends(get_caller)],
    emile_household: Annotated[
        int | None,
        Header(description="Household id, needed only by users who belong to several households."),
    ] = None,
) -> HouseholdInfo:
    """The household the request acts on: the header's, or the caller's only household."""
    with db.session() as session:
        if emile_household is not None:
            household = session.get(Household, emile_household)
            if household is None or not accounts.is_member(session, household.id, caller.user_id):
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"no household {emile_household} for this user")
            return HouseholdInfo.of(household)
        households = accounts.households_of(session, caller.user_id)
    if not households:
        raise HTTPException(status.HTTP_409_CONFLICT, "this user belongs to no household yet")
    if len(households) > 1:
        ids = ", ".join(str(h.id) for h in households)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"several households ({ids}): choose with Emile-Household")
    return HouseholdInfo.of(households[0])


def require_admin(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    if settings.admin_key is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin API disabled: set EMILE_ADMIN_KEY to enable it")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), settings.admin_key.get_secret_value().encode()
    ):
        raise _unauthorized("admin key required")


DbDep = Annotated[Database, Depends(get_db)]
NowDep = Annotated[datetime.datetime, Depends(get_now)]
LimitsDep = Annotated[Limits, Depends(get_limits)]
CallerDep = Annotated[accounts.Caller, Depends(get_caller)]
HouseholdDep = Annotated[HouseholdInfo, Depends(get_household)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
