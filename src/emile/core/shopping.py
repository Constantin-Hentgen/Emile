"""Shopping list rules.

The central rule is **ticked = bought**: ticking an item puts it in the stock, unticking takes it out again. Stock
entries created that way point back to the list item (``list_id`` + ``list_index``), which is what makes unticking
and resetting exact.

There is one *current* list per household. A new list replaces it. Ticking by position addresses the list by id,
and editing by name gives the list a new id: a client still showing the old positions (chat buttons) gets
:class:`StaleList` instead of ticking the wrong item.
"""

import datetime
from collections.abc import Sequence

from emile.core import stock
from emile.core.errors import InvalidInput, ItemNotFound, LimitExceeded, NoCurrentList, StaleList
from emile.core.limits import DEFAULT_LIMITS, Limits
from emile.core.models import EditSummary, Kitchen, ListItem, NewItem, ShoppingList, Source
from emile.core.text import (
    MAX_AISLE,
    MAX_COVERAGE,
    MAX_NAME,
    MAX_QTY,
    MAX_SHOP,
    clean,
    keeps_days,
    name_key,
    positive_int,
)


def _item(new: NewItem, limits: Limits) -> ListItem | None:
    name = clean(new.name, MAX_NAME)
    if name is None:
        return None
    return ListItem(
        name=name,
        qty=clean(new.qty, MAX_QTY),
        aisle=clean(new.aisle, MAX_AISLE),
        keeps=keeps_days(new.keeps, limits),
    )


def current(kitchen: Kitchen, list_id: int | None = None) -> ShoppingList:
    """The current list; with ``list_id``, only if it is still that list (else :class:`StaleList`)."""
    lst = kitchen.current_list
    if lst is None:
        raise NoCurrentList("no current shopping list: create one first")
    if list_id is not None and lst.id != list_id:
        raise StaleList(f"list {list_id} is no longer current (current: {lst.id})")
    return lst


def new_list(
    kitchen: Kitchen,
    items: Sequence[NewItem],
    *,
    now: datetime.datetime,
    days: object = None,
    coverage: str | None = None,
    shop: str | None = None,
    cost_min: float | None = None,
    cost_max: float | None = None,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> ShoppingList:
    """Create the current list, replacing the previous one; every item starts unticked.

    ``start`` and ``end`` give the shopping slot for the calendar. Without them, the previous list's slot is reused
    if it is still ahead of ``now`` (replacing a list keeps the trip). The calendar event itself is not carried
    over: the new list gets its own, and the sync removes the old one.

    Items without a name are skipped. More items than ``Limits.max_list_items`` is an error rather than a silent
    truncation (Bob truncated), so the client knows to split or shorten the list.
    The previous list's stock entries stay in the stock: those things were bought.
    """
    clean_items = [item for new in items if (item := _item(new, limits)) is not None]
    if not clean_items:
        raise InvalidInput("empty shopping list")
    if len(clean_items) > limits.max_list_items:
        raise LimitExceeded(f"{len(clean_items)} items: a list holds at most {limits.max_list_items}")
    if cost_min is not None and cost_max is not None and cost_max < cost_min:
        cost_min, cost_max = cost_max, cost_min
    if (start is None) != (end is None):
        raise InvalidInput("a shopping slot needs both a start and an end")
    if start is not None and end is not None:
        if start.tzinfo is None or end.tzinfo is None:
            raise InvalidInput("the shopping slot must be timezone-aware")
        if end <= start:
            raise InvalidInput("the shopping slot must end after it starts")
        if end < now:
            raise InvalidInput("the shopping slot is in the past")
    elif (
        (previous := kitchen.current_list) and previous.event_start and previous.event_end and previous.event_end > now
    ):
        start, end = previous.event_start, previous.event_end
    lst = ShoppingList(
        id=kitchen.next_list_id,
        items=clean_items,
        days=positive_int(days),
        coverage=clean(coverage, MAX_COVERAGE),
        shop=clean(shop, MAX_SHOP),
        cost_min=cost_min,
        cost_max=cost_max,
        event_start=start,
        event_end=end,
        created=now,
    )
    kitchen.next_list_id += 1
    kitchen.current_list = lst
    return lst


def toggle(
    kitchen: Kitchen, list_id: int, index: int, *, today: datetime.date, limits: Limits = DEFAULT_LIMITS
) -> ShoppingList:
    """Tick or untick the item at ``index`` of list ``list_id``; ticked items enter the stock."""
    lst = current(kitchen, list_id)
    if not 0 <= index < len(lst.items):
        raise ItemNotFound(f"list {list_id} has no item {index}")
    item = lst.items[index]
    if item.done:
        kitchen.stock = [e for e in kitchen.stock if not (e.list_id == lst.id and e.list_index == index)]
        item.done = False
    else:
        stock.put(
            kitchen,
            item.name,
            item.qty,
            today,
            keeps=item.keeps,
            source=Source.LIST,
            list_id=lst.id,
            list_index=index,
            limits=limits,
        )
        item.done = True
    return lst


def reset(kitchen: Kitchen, list_id: int) -> ShoppingList:
    """Untick everything and take those items back out of the stock ("I ticked the wrong list")."""
    lst = current(kitchen, list_id)
    for item in lst.items:
        item.done = False
    kitchen.stock = [e for e in kitchen.stock if e.list_id != lst.id]
    return lst


def edit(
    kitchen: Kitchen,
    *,
    check: Sequence[str] = (),
    uncheck: Sequence[str] = (),
    remove: Sequence[str] = (),
    add: Sequence[NewItem] = (),
    today: datetime.date,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[ShoppingList, EditSummary]:
    """Edit the current list by name (case-insensitive), in this order: remove, add, check, uncheck.

    The list gets a **new id**; its calendar event, coverage, cost and recipes are kept. In the stock, items that
    stay ticked keep their entry (and purchase date), re-pointed to their new position; newly ticked items enter;
    unticked and removed ones leave.

    Unlike Bob, an item that was already ticked but whose stock entry has since been removed (it was eaten) is *not*
    put back: the edit only changes what it is asked to change.
    """
    old = current(kitchen)
    out = EditSummary()
    # (item, index in the old list or None for new items, ticked before the edit)
    rows: list[tuple[ListItem, int | None, bool]] = [
        (item.model_copy(), n, item.done) for n, item in enumerate(old.items)
    ]

    def find(name: str) -> int | None:
        key = name_key(name)
        return next((n for n, row in enumerate(rows) if name_key(row[0].name) == key), None)

    for name in remove:
        if (pos := find(name)) is None:
            out.not_found.append(name)
        else:
            out.removed.append(rows.pop(pos)[0].name)
    for new in add:
        item = _item(new, limits)
        if item is not None and find(item.name) is None:
            rows.append((item, None, False))
            out.added.append(item.name)
    for names, done, report in ((check, True, out.checked), (uncheck, False, out.unchecked)):
        for name in names:
            if (pos := find(name)) is None:
                out.not_found.append(name)
            else:
                rows[pos][0].done = done
                report.append(rows[pos][0].name)
    if len(rows) > limits.max_list_items:
        raise LimitExceeded(f"{len(rows)} items: a list holds at most {limits.max_list_items}")

    with stock.atomic(kitchen):
        new_id = kitchen.next_list_id
        kitchen.next_list_id += 1
        by_index = {e.list_index: e for e in kitchen.stock if e.list_id == old.id}
        kept: set[int] = set()
        for n, (item, old_index, was_done) in enumerate(rows):
            if not item.done:
                continue
            entry = by_index.get(old_index) if old_index is not None else None
            if entry is not None:
                entry.list_id, entry.list_index = new_id, n
                kept.add(entry.id)
            elif not was_done:
                entry = stock.put(
                    kitchen,
                    item.name,
                    item.qty,
                    today,
                    keeps=item.keeps,
                    source=Source.LIST,
                    list_id=new_id,
                    list_index=n,
                    limits=limits,
                )
                kept.add(entry.id)
        kitchen.stock = [e for e in kitchen.stock if e.id in kept or e.list_id != old.id]
        kitchen.current_list = old.model_copy(update={"id": new_id, "items": [row[0] for row in rows]})
    return kitchen.current_list, out


def set_recipes(kitchen: Kitchen, list_id: int, text: str | None, meals: Sequence[int] = ()) -> ShoppingList:
    """Attach the recipes written for this list, with the ids of the client's meals they describe."""
    lst = current(kitchen, list_id)
    lst.recipes = (text or "").strip() or None
    lst.recipe_meals = list(meals)
    return lst


def attach_event(
    kitchen: Kitchen,
    list_id: int,
    uid: str | None,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> ShoppingList:
    """Remember the calendar event holding the list (and its slot, reused when the list is replaced)."""
    lst = current(kitchen, list_id)
    lst.event_uid, lst.event_start, lst.event_end = uid, start, end
    return lst


def set_slot(
    kitchen: Kitchen, list_id: int, start: datetime.datetime, end: datetime.datetime, *, now: datetime.datetime
) -> ShoppingList:
    """Move the shopping trip: the calendar event keeps its identity and is updated in place."""
    lst = current(kitchen, list_id)
    if start.tzinfo is None or end.tzinfo is None:
        raise InvalidInput("the shopping slot must be timezone-aware")
    if end <= start:
        raise InvalidInput("the shopping slot must end after it starts")
    if end < now:
        raise InvalidInput("the shopping slot is in the past")
    lst.event_start, lst.event_end = start, end
    return lst
