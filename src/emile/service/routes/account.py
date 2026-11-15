"""The caller and their household."""

from fastapi import APIRouter

from emile.schemas import HouseholdOut, HouseholdPatch, Me
from emile.service import accounts
from emile.service.deps import CallerDep, DbDep, HouseholdDep

router = APIRouter(prefix="/v1", tags=["account"])


@router.get("/me")
def me(db: DbDep, caller: CallerDep) -> Me:
    """Who the key belongs to, and their households."""
    with db.session() as session:
        households = [accounts.household_out(session, h) for h in accounts.households_of(session, caller.user_id)]
    return Me(id=caller.user_id, name=caller.name, households=households)


@router.get("/household")
def get_household(db: DbDep, household: HouseholdDep) -> HouseholdOut:
    with db.session() as session:
        return accounts.household_out(session, accounts.find_household(session, household.id))


@router.patch("/household")
def patch_household(body: HouseholdPatch, db: DbDep, household: HouseholdDep) -> HouseholdOut:
    """Change the household's settings (name, time zone, language, currency, number of meal-prep boxes)."""
    with db.session() as session:
        row = accounts.find_household(session, household.id)
        for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(row, field, value)
        session.flush()
        return accounts.household_out(session, row)
