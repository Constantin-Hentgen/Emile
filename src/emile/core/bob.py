"""Convert the kitchen part of a Bob ``state.json`` into a :class:`Kitchen`.

Bob (the Telegram bot Emile was extracted from) keeps everything in one JSON file. Only four keys are read here:
``shopping``, ``pantry``, ``next_shopping_id`` and ``next_pantry_id``. The rest of the file (memories, conversation
history, meals…) is personal data that is none of Emile's business: it is never looked at.

Ids are preserved, so chat buttons Bob already sent (``s:t:{list id}:{index}``, ``k:y:{stock id}``) keep working
after the switch.
"""

import datetime
import re
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from emile.core.errors import InvalidInput, LimitExceeded
from emile.core.limits import DEFAULT_LIMITS, Limits
from emile.core.models import Kitchen, ListItem, ShoppingList, Source, StockEntry
from emile.core.text import MAX_AISLE, MAX_COVERAGE, MAX_NAME, MAX_QTY, clean, keeps_days, name_key, positive_int

_LIST_SOURCE = re.compile(r"^liste (\d+):(\d+)$")  # Bob's stock provenance: "liste {list id}:{item index}"


def _date(value: object) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _datetime(value: object, tz: ZoneInfo) -> datetime.datetime | None:
    """ISO datetime; a naive one is taken as local to the household, as Bob did."""
    try:
        parsed = datetime.datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)


def _number(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int(value: object, default: int) -> int:
    return positive_int(value) or default


def kitchen_from_state(
    state: Mapping[str, Any],
    *,
    now: datetime.datetime,
    tz: ZoneInfo,
    limits: Limits = DEFAULT_LIMITS,
) -> Kitchen:
    """Build the kitchen described by a Bob state. Deterministic: the same state always gives the same kitchen,
    which is what makes the import idempotent.

    Raises :class:`InvalidInput` when the state has neither ``shopping`` nor ``pantry``, and
    :class:`LimitExceeded` when it holds more than this deployment allows.
    """
    if "shopping" not in state and "pantry" not in state:
        raise InvalidInput("not a Bob state: neither 'shopping' nor 'pantry' found")
    today = now.astimezone(tz).date()

    current = None
    raw_list = state.get("shopping")
    if isinstance(raw_list, Mapping):
        items = []
        for raw in raw_list.get("items") or []:
            name = clean(raw.get("name"), MAX_NAME) if isinstance(raw, Mapping) else None
            if name is None:
                continue
            items.append(
                ListItem(
                    name=name,
                    qty=clean(raw.get("qty"), MAX_QTY),
                    aisle=clean(raw.get("aisle"), MAX_AISLE),
                    keeps=keeps_days(raw.get("keeps"), limits),
                    done=bool(raw.get("done")),
                )
            )
        if len(items) > limits.max_list_items:
            raise LimitExceeded(f"{len(items)} items: a list holds at most {limits.max_list_items}")
        if items:
            current = ShoppingList(
                id=_int(raw_list.get("id"), 1),
                items=items,
                days=positive_int(raw_list.get("days")),
                coverage=clean(raw_list.get("coverage"), MAX_COVERAGE),
                cost_min=_number(raw_list.get("cost_min")),
                cost_max=_number(raw_list.get("cost_max")),
                recipes=(str(raw_list.get("recipes") or "").strip() or None),
                recipe_meals=[int(m) for m in raw_list.get("recipe_meals") or [] if str(m).isdigit()],
                event_uid=clean(raw_list.get("event_uid"), 255),
                event_start=_datetime(raw_list.get("event_start"), tz),
                event_end=_datetime(raw_list.get("event_end"), tz),
                created=_datetime(raw_list.get("created"), tz) or now,
            )

    next_stock_id = _int(state.get("next_pantry_id"), 1)
    by_name: dict[str, StockEntry] = {}
    used_ids: set[int] = set()
    for raw in state.get("pantry") or []:
        name = clean(raw.get("name"), MAX_NAME) if isinstance(raw, Mapping) else None
        if name is None:
            continue
        entry_id = positive_int(raw.get("id"))
        if entry_id is None or entry_id in used_ids:  # old Bob entries had no id: number them as Bob does on load
            entry_id = max(next_stock_id, max(used_ids, default=0) + 1)
        used_ids.add(entry_id)
        source, list_id, list_index = Source.IMPORT, None, None
        raw_source = raw.get("source")
        if match := _LIST_SOURCE.match(str(raw_source or "")):
            source, list_id, list_index = Source.LIST, int(match.group(1)), int(match.group(2))
        elif raw_source == "conversation":
            source = Source.MANUAL
        by_name[name_key(name)] = StockEntry(  # names are unique in a stock: the last one wins
            id=entry_id,
            name=name,
            qty=clean(raw.get("qty"), MAX_QTY),
            added=_date(raw.get("added")) or today,
            source=source,
            list_id=list_id,
            list_index=list_index,
            keeps=keeps_days(raw.get("keeps"), limits),
            due=_date(raw.get("due")),
            asked=_date(raw.get("asked")),
            boxes=bool(raw.get("boxes")),
        )
    stock = sorted(by_name.values(), key=lambda e: e.id)
    if len(stock) > limits.max_stock_items:
        raise LimitExceeded(f"{len(stock)} stock entries: a stock holds at most {limits.max_stock_items}")

    return Kitchen(
        current_list=current,
        stock=stock,
        next_list_id=max(_int(state.get("next_shopping_id"), 1), (current.id + 1) if current else 1),
        next_stock_id=max(next_stock_id, max((e.id for e in stock), default=0) + 1),
    )


def merge_into(kitchen: Kitchen, imported: Kitchen) -> bool:
    """Replace ``kitchen``'s list and stock with the imported ones; id counters never go backwards (ids are never
    reused). Returns whether anything changed, so a second import of the same file reports "unchanged"."""
    before = kitchen.model_copy(deep=True)
    kitchen.current_list = imported.current_list
    kitchen.stock = imported.stock
    kitchen.next_list_id = max(kitchen.next_list_id, imported.next_list_id)
    kitchen.next_stock_id = max(kitchen.next_stock_id, imported.next_stock_id)
    return kitchen != before
