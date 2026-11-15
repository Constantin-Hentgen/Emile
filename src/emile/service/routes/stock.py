"""The kitchen stock: read, update, "still there?" checks, expiry, text."""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from emile.core import Kitchen, StockSummary, render, stock
from emile.schemas import (
    AnswerRequest,
    AnswerResponse,
    AskedRequest,
    AskedResponse,
    BoxDish,
    Boxes,
    ExpireRequest,
    ExpireResponse,
    Language,
    StockOut,
    StockUpdateRequest,
    ToCheck,
)
from emile.service import repository
from emile.service.deps import DbDep, HouseholdDep, LimitsDep, NowDep
from emile.service.repository import HouseholdInfo

router = APIRouter(prefix="/v1/stock", tags=["stock"])


def _stock_out(kitchen: Kitchen, info: HouseholdInfo) -> StockOut:
    dishes = stock.boxes_in_use(kitchen.stock)
    used = sum(n for __, n in dishes)
    return StockOut(
        entries=kitchen.stock,
        boxes=Boxes(
            total=info.meal_boxes,
            used=used,
            free=max(0, info.meal_boxes - used),
            dishes=[BoxDish(name=name, boxes=n) for name, n in dishes],
        ),
    )


@router.get("")
def get_stock(db: DbDep, household: HouseholdDep) -> StockOut:
    """Everything in the kitchen, and the meal-prep boxes in use."""
    return repository.read(db, household.id, _stock_out)


@router.post("")
def update_stock(
    body: StockUpdateRequest, db: DbDep, household: HouseholdDep, now: NowDep, limits: LimitsDep
) -> StockSummary:
    """Same as Bob's `update_stock` tool. `remove` first (with `qty_left` when some is left), then `add` (same name
    replaces the entry; `boxes: true` for a batch-cooked dish whose `qty` counts boxes). All or nothing."""
    return repository.mutate(
        db,
        household.id,
        lambda kitchen, info: stock.update(kitchen, body.add, body.remove, today=info.today(now), limits=limits),
    )


@router.get("/text", response_class=PlainTextResponse)
def stock_text(
    db: DbDep,
    household: HouseholdDep,
    now: NowDep,
    style: Literal["compact", "prompt", "boxes"] = "compact",
    lang: Annotated[Language | None, Query(description="Defaults to the household's language.")] = None,
) -> str:
    """The stock as text: `compact` for a chat reply, `prompt` for a language model's context, `boxes` for the
    meal-prep box summary."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> str:
        language = lang or info.language
        if style == "boxes":
            return render.boxes_text(kitchen.stock, info.meal_boxes, language)  # type: ignore[arg-type]
        return render.stock_text(kitchen.stock, info.today(now), language, compact=style == "compact")  # type: ignore[arg-type]

    return repository.read(db, household.id, run)


@router.get("/to-check")
def to_check(
    db: DbDep,
    household: HouseholdDep,
    now: NowDep,
    date: Annotated[datetime.date | None, Query(description="Defaults to today in the household's time zone.")] = None,
) -> ToCheck:
    """Perishables that reached their date and were not asked about yet. Ask, then call `POST /v1/stock/asked`."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> ToCheck:
        day = date or info.today(now)
        return ToCheck(date=day, entries=stock.to_check(kitchen, day))

    return repository.read(db, household.id, run)


@router.post("/asked")
def mark_asked(body: AskedRequest, db: DbDep, household: HouseholdDep, now: NowDep) -> AskedResponse:
    """Record that "still there?" was asked; unanswered past the grace period, the entries leave the stock."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> AskedResponse:
        day = body.date or info.today(now)
        return AskedResponse(date=day, entries=stock.mark_asked(kitchen, body.ids, day))

    return repository.mutate(db, household.id, run)


@router.post("/{entry_id}/answer")
def answer(entry_id: int, body: AnswerRequest, db: DbDep, household: HouseholdDep, now: NowDep) -> AnswerResponse:
    """ "Still there": the next check comes in half the keeping time. "Finished": removed. 404 if already gone."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> AnswerResponse:
        entry = stock.answer(kitchen, entry_id, body.still_there, body.date or info.today(now))
        if entry is None:
            raise HTTPException(404, f"no stock entry {entry_id} (already removed?)")
        return AnswerResponse(entry=entry, removed=not body.still_there)

    return repository.mutate(db, household.id, run)


@router.post("/expire")
def expire(
    db: DbDep, household: HouseholdDep, now: NowDep, limits: LimitsDep, body: ExpireRequest | None = None
) -> ExpireResponse:
    """Remove the entries whose "still there?" question went unanswered for the grace period."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> ExpireResponse:
        day = (body.date if body else None) or info.today(now)
        return ExpireResponse(date=day, removed=stock.expire(kitchen, day, limits))

    return repository.mutate(db, household.id, run)
