"""Pure domain rules for shopping lists and kitchen stock.

Nothing in this package may import a web framework, an ORM or a network client at import time: the rules are
deterministic functions over plain data (a :class:`Kitchen`), so they can be unit-tested exhaustively and reused by
any client. The caller supplies ``today`` / ``now`` in the household's time zone.

Typical use::

    from emile.core import Kitchen, NewItem, shopping, stock

    kitchen = Kitchen()
    lst = shopping.new_list(kitchen, [NewItem(name="Bananas", qty="7", keeps=6)], now=now)
    shopping.toggle(kitchen, lst.id, 0, today=today)   # bought: now in kitchen.stock
"""

from emile.core import render, shopping, stock
from emile.core.errors import EmileError, ItemNotFound, LimitExceeded, NoCurrentList, StaleList
from emile.core.limits import DEFAULT_LIMITS, Limits
from emile.core.models import (
    EditSummary,
    Kitchen,
    ListItem,
    NewItem,
    ShoppingList,
    Source,
    StockAdd,
    StockEntry,
    StockRemove,
    StockSummary,
)
from emile.core.text import keeps_days

__all__ = [
    "DEFAULT_LIMITS",
    "EditSummary",
    "EmileError",
    "ItemNotFound",
    "Kitchen",
    "LimitExceeded",
    "Limits",
    "ListItem",
    "NewItem",
    "NoCurrentList",
    "ShoppingList",
    "Source",
    "StaleList",
    "StockAdd",
    "StockEntry",
    "StockRemove",
    "StockSummary",
    "keeps_days",
    "render",
    "shopping",
    "stock",
]
