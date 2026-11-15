"""Administration with ``EMILE_ADMIN_KEY``: the same operations as the CLI, for setups without a shell."""

from fastapi import APIRouter, Depends
from sqlalchemy import select

from emile.schemas import HouseholdCreate, HouseholdOut, KeyCreate, KeyCreated, MemberAdd, UserCreate, UserOut
from emile.service import accounts
from emile.service.deps import DbDep, NowDep, SettingsDep, require_admin
from emile.service.tables import Household, User

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, name=user.name, created=user.created_at)


@router.get("/users")
def list_users(db: DbDep) -> list[UserOut]:
    with db.session() as session:
        return [_user_out(u) for u in session.scalars(select(User).order_by(User.id))]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, db: DbDep, now: NowDep) -> UserOut:
    with db.session() as session:
        return _user_out(accounts.create_user(session, body.name, now))


@router.post("/users/{user_id}/keys", status_code=201)
def create_key(user_id: int, body: KeyCreate, db: DbDep, now: NowDep) -> KeyCreated:
    """A new API key; the `key` field is shown this once."""
    with db.session() as session:
        key, secret = accounts.create_key(session, accounts.find_user(session, user_id), body.label, now)
        return KeyCreated(id=key.id, user_id=key.user_id, label=key.label, prefix=key.prefix, key=secret)


@router.post("/keys/{key_id}/revoke", status_code=204)
def revoke_key(key_id: int, db: DbDep, now: NowDep) -> None:
    with db.session() as session:
        accounts.revoke_key(session, key_id, now)


@router.get("/households")
def list_households(db: DbDep) -> list[HouseholdOut]:
    with db.session() as session:
        return [accounts.household_out(session, h) for h in session.scalars(select(Household).order_by(Household.id))]


@router.post("/households", status_code=201)
def create_household(body: HouseholdCreate, db: DbDep, now: NowDep, settings: SettingsDep) -> HouseholdOut:
    with db.session() as session:
        household = accounts.create_household(
            session,
            body.name,
            timezone=body.timezone or settings.default_timezone,
            language=body.language or settings.default_language,
            currency=body.currency or settings.default_currency,
            meal_boxes=body.meal_boxes,
            members=[accounts.find_user(session, uid) for uid in body.members],
            now=now,
        )
        return accounts.household_out(session, household)


@router.post("/households/{household_id}/members")
def add_member(household_id: int, body: MemberAdd, db: DbDep, now: NowDep) -> HouseholdOut:
    with db.session() as session:
        household = accounts.find_household(session, household_id)
        accounts.add_member(session, household, accounts.find_user(session, body.user_id), now)
        return accounts.household_out(session, household)
