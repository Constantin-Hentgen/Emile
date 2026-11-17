"""The shopping list: create, read, edit by name, tick by position, reset, recipes, text."""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from emile.core import Kitchen, ShoppingList, render, shopping
from emile.schemas import EditListRequest, EditListResponse, Language, NewListRequest, RecipesRequest, SlotRequest
from emile.service import repository
from emile.service.deps import DbDep, HouseholdDep, LimitsDep, NowDep
from emile.service.repository import HouseholdInfo

router = APIRouter(prefix="/v1/lists", tags=["shopping list"])


def _span(
    start_time: datetime.datetime, duration_minutes: int, info: HouseholdInfo
) -> tuple[datetime.datetime, datetime.datetime]:
    """A request's start time (local to the household if it has no zone) and duration as an aware (start, end)."""
    start = start_time if start_time.tzinfo else start_time.replace(tzinfo=info.timezone)
    start = start.astimezone(datetime.UTC)
    return start, start + datetime.timedelta(minutes=duration_minutes)


@router.post("", status_code=201)
def create_list(
    body: NewListRequest, db: DbDep, household: HouseholdDep, now: NowDep, limits: LimitsDep
) -> ShoppingList:
    """Make this the household's current list, replacing the previous one (whose purchases stay in the stock).

    Accepts the arguments of Bob's `save_shopping_list` tool (`days_covered`, `estimated_cost_min_eur`, …).
    """

    def run(kitchen: Kitchen, info: HouseholdInfo) -> ShoppingList:
        start, end = _span(body.start_time, body.duration_minutes, info) if body.start_time else (None, None)
        return shopping.new_list(
            kitchen,
            body.items,
            now=now,
            days=body.days,
            coverage=body.coverage,
            shop=body.shop,
            cost_min=body.cost_min,
            cost_max=body.cost_max,
            start=start,
            end=end,
            limits=limits,
        )

    return repository.mutate(db, household.id, run)


@router.get("/current")
def get_current(db: DbDep, household: HouseholdDep) -> ShoppingList:
    """The current list (404 `no_current_list` if there is none)."""
    return repository.read(db, household.id, lambda kitchen, info: shopping.current(kitchen))


@router.patch("/current")
def edit_current(
    body: EditListRequest, db: DbDep, household: HouseholdDep, now: NowDep, limits: LimitsDep
) -> EditListResponse:
    """Edit by name, case-insensitive: remove, add, check (= bought: enters the stock), uncheck.

    The list gets a **new id**, so position-based actions on the old id are refused (409 `stale_list`).
    """

    def run(kitchen: Kitchen, info: HouseholdInfo) -> EditListResponse:
        lst, summary = shopping.edit(
            kitchen,
            check=body.check,
            uncheck=body.uncheck,
            remove=body.remove,
            add=body.add,
            today=info.today(now),
            limits=limits,
        )
        return EditListResponse(shopping_list=lst, summary=summary)

    return repository.mutate(db, household.id, run)


@router.post("/{list_id}/items/{index}/toggle")
def toggle_item(
    list_id: int, index: int, db: DbDep, household: HouseholdDep, now: NowDep, limits: LimitsDep
) -> ShoppingList:
    """Tick or untick the item at position `index` (0-based); ticked = bought, so it enters the stock.

    Refused with 409 `stale_list` if `list_id` is no longer the current list.
    """
    return repository.mutate(
        db,
        household.id,
        lambda kitchen, info: shopping.toggle(kitchen, list_id, index, today=info.today(now), limits=limits),
    )


@router.post("/{list_id}/reset")
def reset_list(list_id: int, db: DbDep, household: HouseholdDep) -> ShoppingList:
    """Untick everything and take those items back out of the stock."""
    return repository.mutate(db, household.id, lambda kitchen, info: shopping.reset(kitchen, list_id))


@router.put("/{list_id}/recipes")
def put_recipes(list_id: int, body: RecipesRequest, db: DbDep, household: HouseholdDep) -> ShoppingList:
    """Attach the recipes written for this list, with the client's meal ids for them."""
    return repository.mutate(
        db, household.id, lambda kitchen, info: shopping.set_recipes(kitchen, list_id, body.text, body.meals)
    )


@router.get("/current/text", response_class=PlainTextResponse)
def current_text(
    db: DbDep,
    household: HouseholdDep,
    style: Literal["checklist", "prompt", "summary"] = "checklist",
    lang: Annotated[Language | None, Query(description="Defaults to the household's language.")] = None,
) -> str:
    """The list as text: an aisle-grouped checklist, a block for a language model's context, or the summary line
    ("7 days · €55–70 estimated")."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> str:
        language = lang or info.language
        if style == "prompt":  # "none" when there is no list: a prompt can say so
            return render.list_text(kitchen.current_list, language, info.timezone)  # type: ignore[arg-type]
        lst = shopping.current(kitchen)
        if style == "summary":
            return render.summary_line(lst, language, info.currency)  # type: ignore[arg-type]
        return render.checklist(lst.items, language, info.aisle_order)  # type: ignore[arg-type]

    return repository.read(db, household.id, run)


@router.put("/{list_id}/slot")
def set_slot(list_id: int, body: SlotRequest, db: DbDep, household: HouseholdDep, now: NowDep) -> ShoppingList:
    """Move the shopping trip. Its calendar event is updated in place at the next sync.

    Refused with 409 `stale_list` if `list_id` is no longer the current list.
    """

    def run(kitchen: Kitchen, info: HouseholdInfo) -> ShoppingList:
        start, end = _span(body.start_time, body.duration_minutes, info)
        return shopping.set_slot(kitchen, list_id, start, end, now=now)

    return repository.mutate(db, household.id, run)
