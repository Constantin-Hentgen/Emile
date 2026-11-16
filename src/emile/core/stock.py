"""Stock rules: what is in the kitchen, how perishables are checked, and batch-cooked boxes.

Lifecycle of a perishable entry:

1. it enters the stock with ``keeps`` days → ``due = added + keeps``;
2. once ``due`` is reached, the client asks "still there?" (:func:`to_check`, then :func:`mark_asked`);
3. "still there" pushes ``due`` back by half the keeping time (:func:`answer`), "finished" removes it;
4. unanswered for ``Limits.ask_grace_days``, it leaves the stock (:func:`expire`): an unanswered question most
   likely means it was eaten or thrown away, and a stale stock misleads whoever plans the next meals.
"""

import contextlib
import datetime
import re
from collections.abc import Iterable, Iterator, Sequence

from emile.core.errors import InvalidInput, LimitExceeded
from emile.core.limits import DEFAULT_LIMITS, Limits
from emile.core.models import KeepsValue, Kitchen, Source, StockAdd, StockEntry, StockRemove, StockSummary
from emile.core.quantity import Lang, Quantity, format_quantity, parse, subtract
from emile.core.text import MAX_NAME, MAX_QTY, clean, keeps_days, name_key


@contextlib.contextmanager
def atomic(kitchen: Kitchen) -> Iterator[None]:
    """Run a multi-step change all-or-nothing: on any error, the kitchen is restored to its previous state.

    Rules raise on limits half-way through a batch (the 121st stock entry); callers must never see half a batch.
    """
    snapshot = kitchen.model_copy(deep=True)
    try:
        yield
    except BaseException:
        for field in type(kitchen).model_fields:
            setattr(kitchen, field, getattr(snapshot, field))
        raise


def find(kitchen: Kitchen, name: str) -> StockEntry | None:
    """The entry with this name (case-insensitive), if any."""
    key = name_key(name)
    return next((e for e in kitchen.stock if name_key(e.name) == key), None)


def put(
    kitchen: Kitchen,
    name: str,
    qty: str | None,
    today: datetime.date,
    *,
    keeps: KeepsValue = None,
    source: Source = Source.MANUAL,
    list_id: int | None = None,
    list_index: int | None = None,
    boxes: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> StockEntry:
    """Add an entry, replacing any entry with the same name (it was bought again: new date, new quantity).

    Raises :class:`LimitExceeded` when the stock is full; nothing is changed then.
    """
    clean_name = clean(name, MAX_NAME)
    if clean_name is None:
        raise InvalidInput("a stock entry needs a name")
    others = [e for e in kitchen.stock if name_key(e.name) != name_key(clean_name)]
    if len(others) >= limits.max_stock_items:
        raise LimitExceeded(f"stock full ({limits.max_stock_items} entries): remove what is finished first")
    days = keeps_days(keeps, limits)
    entry = StockEntry(
        id=kitchen.next_stock_id,
        name=clean_name,
        qty=clean(qty, MAX_QTY),
        added=today,
        source=source,
        list_id=list_id,
        list_index=list_index,
        keeps=days,
        due=today + datetime.timedelta(days=days) if days else None,
        boxes=boxes,
    )
    kitchen.next_stock_id += 1
    kitchen.stock = [*others, entry]
    return entry


def label(entry: StockEntry) -> str:
    """ "Rice (1 kg)", or just "Rice"."""
    return entry.name + (f" ({entry.qty})" if entry.qty else "")


def update(
    kitchen: Kitchen,
    add: Sequence[StockAdd] = (),
    remove: Sequence[StockRemove] = (),
    *,
    today: datetime.date,
    limits: Limits = DEFAULT_LIMITS,
    lang: Lang = "fr",
) -> StockSummary:
    """Apply what the household reports: removals first (so "finished the rice, bought rice" ends with rice), then
    additions. All-or-nothing on errors.

    ``lang`` is the household's language, used to write a quantity left after a ``qty_used`` (``1,5 kg``).
    """
    out = StockSummary()
    with atomic(kitchen):
        for r in remove:
            name = clean(r.name, MAX_NAME) or ""
            entry = find(kitchen, name) if name else None
            if entry is None:
                out.not_found.append(name)
            elif qty_used := clean(r.qty_used, MAX_QTY):
                if clean(r.qty_left, MAX_QTY):
                    raise InvalidInput(f"{entry.name}: give qty_left or qty_used, not both")
                left = _use(entry, qty_used)
                if left is None:
                    kitchen.stock.remove(entry)
                    out.removed.append(entry.name)
                else:
                    entry.qty = format_quantity(left, lang)
                    out.updated.append(label(entry))
            elif qty_left := clean(r.qty_left, MAX_QTY):
                entry.qty = qty_left
                out.updated.append(label(entry))
            else:
                kitchen.stock.remove(entry)
                out.removed.append(entry.name)
        for a in add:
            if clean(a.name, MAX_NAME) is None:
                continue
            entry = put(kitchen, a.name, a.qty, today, keeps=a.keeps, boxes=a.boxes, limits=limits)
            out.added.append(label(entry))
    return out


def _use(entry: StockEntry, used_text: str) -> Quantity | None:
    """What is left of ``entry`` after using ``used_text``; ``None`` when nothing is left."""
    have, used = parse(entry.qty), parse(used_text)
    if have is None:
        raise InvalidInput(f"{entry.name}: the quantity in stock ({entry.qty or 'none'}) cannot be read; use qty_left")
    if used is None or used.amount <= 0:
        raise InvalidInput(f"{entry.name}: the quantity used ({used_text}) cannot be read")
    return subtract(have, used)


def remove_ids(kitchen: Kitchen, ids: Iterable[int]) -> list[StockEntry]:
    """Remove entries by id; returns the removed entries."""
    wanted = set(ids)
    gone = [e for e in kitchen.stock if e.id in wanted]
    kitchen.stock = [e for e in kitchen.stock if e.id not in wanted]
    return gone


# --- "still there?" ---------------------------------------------------------------------------------------------


def to_check(kitchen: Kitchen, today: datetime.date) -> list[StockEntry]:
    """Perishables that reached their date and were not asked about yet."""
    return [e for e in kitchen.stock if e.due is not None and e.due <= today and e.asked is None]


def mark_asked(kitchen: Kitchen, ids: Iterable[int], today: datetime.date) -> list[StockEntry]:
    """Record that "still there?" was asked; starts the grace period. Returns the entries marked."""
    wanted = set(ids)
    marked = [e for e in kitchen.stock if e.id in wanted]
    for e in marked:
        e.asked = today
    return marked


def answer(kitchen: Kitchen, entry_id: int, still_there: bool, today: datetime.date) -> StockEntry | None:
    """Apply the answer. Still there: next check in half the keeping time (at least a day), since it is getting old.
    Finished: removed. Returns the entry as it now is (or as it was, if removed); ``None`` if it is already gone,
    which is a normal outcome (answered twice, or expired meanwhile)."""
    entry = next((e for e in kitchen.stock if e.id == entry_id), None)
    if entry is None:
        return None
    if still_there:
        entry.due = today + datetime.timedelta(days=max(1, (entry.keeps or 2) // 2))
        entry.asked = None
    else:
        kitchen.stock.remove(entry)
    return entry


def expire(kitchen: Kitchen, today: datetime.date, limits: Limits = DEFAULT_LIMITS) -> list[StockEntry]:
    """Remove entries whose question has gone unanswered for the grace period; returns them."""
    limit = today - datetime.timedelta(days=limits.ask_grace_days)
    return remove_ids(kitchen, [e.id for e in kitchen.stock if e.asked is not None and e.asked <= limit])


# --- batch-cooked boxes -----------------------------------------------------------------------------------------

_LEADING_NUMBER = re.compile(r"\s*(\d+)")


def box_count(qty: str | None) -> int:
    """ "2 boxes" → 2. Without a readable number, one box: a dish in the stock occupies at least one."""
    match = _LEADING_NUMBER.match(qty or "")
    return int(match.group(1)) if match else 1


def boxes_in_use(stock: Iterable[StockEntry]) -> list[tuple[str, int]]:
    """Batch-cooked dishes still in the stock: ``[(name, boxes)]``."""
    return [(e.name, box_count(e.qty)) for e in stock if e.boxes]


def free_boxes(stock: Iterable[StockEntry], total: int) -> int:
    """Boxes still free out of the household's ``total``, so a planner never cooks more portions than fit."""
    return max(0, total - sum(n for __, n in boxes_in_use(stock)))
