"""One-shot imports from other systems."""

import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query

from emile.core import Kitchen, Limits, bob
from emile.schemas import ImportResult
from emile.service import repository
from emile.service.db import Database
from emile.service.deps import DbDep, HouseholdDep, LimitsDep, NowDep
from emile.service.repository import HouseholdInfo, WouldOverwrite

router = APIRouter(prefix="/v1/import", tags=["import"])


def import_bob_state(
    db: Database, household_id: int, state: dict[str, Any], *, now: datetime.datetime, limits: Limits, replace: bool
) -> ImportResult:
    """Shared by the endpoint and the CLI."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> ImportResult:
        imported = bob.kitchen_from_state(state, now=now, tz=info.timezone, limits=limits)
        empty = kitchen.current_list is None and not kitchen.stock
        same = kitchen.current_list == imported.current_list and kitchen.stock == imported.stock
        if not (empty or same or replace):
            raise WouldOverwrite(
                "this household already has a list or a stock that differs from the import: "
                "pass replace=true to overwrite it"
            )
        changed = bob.merge_into(kitchen, imported)
        return ImportResult(
            changed=changed,
            list_id=kitchen.current_list.id if kitchen.current_list else None,
            list_items=len(kitchen.current_list.items) if kitchen.current_list else 0,
            stock_entries=len(kitchen.stock),
            next_list_id=kitchen.next_list_id,
            next_stock_id=kitchen.next_stock_id,
        )

    return repository.mutate(db, household_id, run)


@router.post("/bob")
def import_bob(
    state: Annotated[dict[str, Any], Body(description="A Bob `state.json` (whole file, or just the kitchen keys).")],
    db: DbDep,
    household: HouseholdDep,
    now: NowDep,
    limits: LimitsDep,
    replace: Annotated[bool, Query(description="Overwrite a kitchen that already holds different data.")] = False,
) -> ImportResult:
    """Import the shopping list, the stock and the id counters of a Bob `state.json`.

    Only `shopping`, `pantry`, `next_shopping_id` and `next_pantry_id` are read; everything else in the file is
    ignored and never stored. Ids are kept, so buttons Bob already sent keep working. **Idempotent**: importing the
    same file again changes nothing. Refused with 409 `would_overwrite` if the household already holds different
    data, unless `replace=true`.
    """
    return import_bob_state(db, household.id, state, now=now, limits=limits, replace=replace)
