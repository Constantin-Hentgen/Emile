"""Load and save one household's kitchen as an aggregate, with optimistic locking.

Every write is conditional on the household's ``version`` being the one that was read. Two members ticking items
at the same moment therefore never overwrite each other: the second write fails, and :func:`mutate` replays it on
the fresh state. The rules are deterministic, so replaying is always safe.
"""

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from emile.core import Kitchen, ListItem, ShoppingList, Source, StockEntry
from emile.service.db import Database
from emile.service.tables import Household, ListItemRow, ShoppingListRow, StockRow

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


class Conflict(Exception):
    """Someone else wrote this household's kitchen since it was read."""


class Busy(Exception):
    """The kitchen kept changing (or the database stayed locked) through every retry."""


@dataclass(frozen=True, slots=True)
class HouseholdInfo:
    """What the rules and texts need to know about a household, outside the kitchen itself."""

    id: int
    name: str
    timezone: ZoneInfo
    language: str
    currency: str
    meal_boxes: int

    @classmethod
    def of(cls, row: Household) -> "HouseholdInfo":
        return cls(row.id, row.name, ZoneInfo(row.timezone), row.language, row.currency, row.meal_boxes)

    def today(self, now: datetime.datetime) -> datetime.date:
        """The household's local date at ``now`` (an aware datetime)."""
        return now.astimezone(self.timezone).date()


def load(session: Session, household: Household) -> Kitchen:
    lst_row = session.get(ShoppingListRow, household.id)
    current = None
    if lst_row is not None:
        items = session.scalars(
            select(ListItemRow).where(ListItemRow.household_id == household.id).order_by(ListItemRow.position)
        )
        current = ShoppingList(
            id=lst_row.list_id,
            items=[ListItem(name=i.name, qty=i.qty, aisle=i.aisle, keeps=i.keeps, done=i.done) for i in items],
            days=lst_row.days,
            coverage=lst_row.coverage,
            cost_min=lst_row.cost_min,
            cost_max=lst_row.cost_max,
            recipes=lst_row.recipes,
            recipe_meals=list(lst_row.recipe_meals or []),
            event_uid=lst_row.event_uid,
            event_start=lst_row.event_start,
            event_end=lst_row.event_end,
            created=lst_row.created_at,
        )
    entries = session.scalars(select(StockRow).where(StockRow.household_id == household.id).order_by(StockRow.entry_id))
    stock = [
        StockEntry(
            id=e.entry_id,
            name=e.name,
            qty=e.qty,
            added=e.added,
            source=Source(e.source),
            list_id=e.list_id,
            list_index=e.list_index,
            keeps=e.keeps,
            due=e.due,
            asked=e.asked,
            boxes=e.boxes,
        )
        for e in entries
    ]
    return Kitchen(
        current_list=current,
        stock=stock,
        next_list_id=household.next_list_id,
        next_stock_id=household.next_stock_id,
    )


def save(session: Session, household_id: int, kitchen: Kitchen, expected_version: int) -> None:
    """Replace the stored kitchen with ``kitchen`` if nobody wrote since ``expected_version``; else :class:`Conflict`.

    Rows are rewritten wholesale: a kitchen is at most a few hundred small rows, and this keeps the mapping trivial.
    """
    bumped = session.execute(
        update(Household)
        .where(Household.id == household_id, Household.version == expected_version)
        .values(
            version=expected_version + 1,
            next_list_id=kitchen.next_list_id,
            next_stock_id=kitchen.next_stock_id,
        )
        .execution_options(synchronize_session=False)
    )
    if bumped.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict(f"household {household_id} changed meanwhile")
    session.execute(delete(ListItemRow).where(ListItemRow.household_id == household_id))
    session.execute(delete(ShoppingListRow).where(ShoppingListRow.household_id == household_id))
    session.execute(delete(StockRow).where(StockRow.household_id == household_id))
    session.flush()
    if (lst := kitchen.current_list) is not None:
        session.add(
            ShoppingListRow(
                household_id=household_id,
                list_id=lst.id,
                days=lst.days,
                coverage=lst.coverage,
                cost_min=lst.cost_min,
                cost_max=lst.cost_max,
                recipes=lst.recipes,
                recipe_meals=list(lst.recipe_meals),
                event_uid=lst.event_uid,
                event_start=lst.event_start,
                event_end=lst.event_end,
                created_at=lst.created,
            )
        )
        session.flush()  # the list row must exist before its items (foreign key)
        session.add_all(
            ListItemRow(
                household_id=household_id,
                position=n,
                name=i.name,
                qty=i.qty,
                aisle=i.aisle,
                keeps=i.keeps,
                done=i.done,
            )
            for n, i in enumerate(lst.items)
        )
    session.add_all(
        StockRow(
            household_id=household_id,
            entry_id=e.id,
            name=e.name,
            qty=e.qty,
            added=e.added,
            source=e.source.value,
            list_id=e.list_id,
            list_index=e.list_index,
            keeps=e.keeps,
            due=e.due,
            asked=e.asked,
            boxes=e.boxes,
        )
        for e in kitchen.stock
    )


def read[T](database: Database, household_id: int, fn: Callable[[Kitchen, HouseholdInfo], T]) -> T:
    """Run a read-only function on the household's kitchen."""
    with database.session() as session:
        household = session.get_one(Household, household_id)
        return fn(load(session, household), HouseholdInfo.of(household))


def mutate[T](database: Database, household_id: int, fn: Callable[[Kitchen, HouseholdInfo], T]) -> T:
    """Apply ``fn`` to the kitchen and save it, retrying on concurrent writes.

    ``fn`` may run more than once, so it must only touch the kitchen it is given. Domain errors raised by ``fn``
    abort the transaction: nothing is saved.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with database.session() as session:
                household = session.get_one(Household, household_id)
                version = household.version
                kitchen = load(session, household)
                result = fn(kitchen, HouseholdInfo.of(household))
                save(session, household_id, kitchen, version)
            return result
        except Conflict:
            logger.info("household %s: concurrent write, retrying (%s/%s)", household_id, attempt, MAX_ATTEMPTS)
        except OperationalError as e:  # SQLite: "database is locked" when two writers race
            if "locked" not in str(e).lower():
                raise
            logger.info("household %s: database locked, retrying (%s/%s)", household_id, attempt, MAX_ATTEMPTS)
    raise Busy(f"household {household_id} is being changed by someone else: try again")
