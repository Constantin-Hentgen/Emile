"""Aisle order of a shop, learned from the order in which its aisles are started.

When an item is ticked and its aisle had no ticked item yet, that is the aisle's *first tick*. Its position (0 = the
first aisle started, 1 = the last) is blended into a smoothed score per aisle. Sorting by score gives the layout of
the shop. Smoothing keeps a few trips from being enough to forget a long history, and the cap on the count lets a
changed layout win within a handful of trips.
"""

from collections.abc import Mapping

from emile.core.models import ShoppingList
from emile.core.text import name_key

MEMORY = 10
"""Trips remembered at most: the weight of history in the average."""
MIN_SEEN = 2
"""Observations needed before a score is used to order anything."""


def key(aisle: str) -> str:
    return name_key(aisle)


def first_ticks(before: ShoppingList | None, after: ShoppingList | None) -> dict[str, float]:
    """Aisle key → position (0..1) for each aisle whose first item was ticked between ``before`` and ``after``.

    Only the same list counts (same id); items without an aisle say nothing about the shop.
    """
    if before is None or after is None or before.id != after.id or len(before.items) != len(after.items):
        return {}
    started = {key(i.aisle) for i in before.items if i.done and i.aisle}
    aisles = {key(i.aisle) for i in after.items if i.aisle}
    new = [key(i.aisle) for b, i in zip(before.items, after.items, strict=True) if i.done and not b.done and i.aisle]
    out: dict[str, float] = {}
    for aisle in new:
        if aisle in started:
            continue
        out[aisle] = len(started - {aisle}) / (len(aisles) - 1) if len(aisles) > 1 else 0.0
        started.add(aisle)
    return out


def blend(score: float, seen: int, position: float) -> tuple[float, int]:
    """The new (score, count) after one more observation."""
    n = min(seen, MEMORY)
    return (score * n + position) / (n + 1), min(seen + 1, MEMORY)


def order(ranks: Mapping[str, tuple[float, int]]) -> dict[str, float]:
    """Aisle key → score, keeping only the aisles seen often enough to be trusted."""
    return {aisle: score for aisle, (score, seen) in ranks.items() if seen >= MIN_SEEN}
