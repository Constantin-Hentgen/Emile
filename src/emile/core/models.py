"""Data model of one household's kitchen: the current shopping list and the stock.

The models are plain pydantic objects with no I/O: the service persists them, a library user may keep them in
memory or in a JSON file. Rules live in :mod:`emile.core.shopping` and :mod:`emile.core.stock`.
"""

import datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field

from emile.core.quantity import Measure, parse

# Inputs come from clients (often an LLM's tool call): accept Bob's field names as aliases so its tool arguments
# can be forwarded unchanged (`quantity`, `keeps_days`).
_INPUT = ConfigDict(populate_by_name=True, extra="ignore")
_QTY = AliasChoices("qty", "quantity")
_KEEPS = AliasChoices("keeps", "keeps_days")

type KeepsValue = int | float | str | None
"""Raw keeping time as sent by a client; normalised by :func:`emile.core.text.keeps_days`."""


class Source(StrEnum):
    """Where a stock entry comes from."""

    LIST = "list"
    """Ticked on the shopping list (``list_id`` and ``list_index`` say which item)."""
    MANUAL = "manual"
    """Added directly ("I have rice and eggs", a batch of boxes)."""
    IMPORT = "import"
    """Imported from another system, with no link to a current list."""


class ListItem(BaseModel):
    """One line of a shopping list. ``done`` means bought: the item is in the stock."""

    name: str
    qty: str | None = None
    aisle: str | None = None
    keeps: int | None = None
    """Days the product keeps once bought (perishables only)."""
    done: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measure(self) -> Measure | None:
        """``qty`` read as a structured quantity, when it is one of the known forms (derived, never stored)."""
        quantity = parse(self.qty)
        return quantity.measure() if quantity else None


class ShoppingList(BaseModel):
    """The household's current shopping list.

    The id changes on every edit by name, so that clients addressing items by position (chat buttons) can detect
    that their view is out of date. Ticking or resetting does not change it: positions are unchanged.
    """

    id: int
    items: list[ListItem] = Field(default_factory=list)
    days: int | None = None
    """Number of days the list covers."""
    coverage: str | None = None
    """What the list covers, e.g. "breakfasts, lunch boxes, dinners; 2 people"."""
    cost_min: float | None = None
    """Estimated total cost range, in the household's currency."""
    cost_max: float | None = None
    recipes: str | None = None
    """Recipes text attached to the list (written by the client)."""
    recipe_meals: list[int] = Field(default_factory=list)
    """Ids of the client's meals linked to those recipes (e.g. "cooked" buttons)."""
    event_uid: str | None = None
    """CalDAV event holding this list, if synced."""
    event_start: datetime.datetime | None = None
    event_end: datetime.datetime | None = None
    created: datetime.datetime


class StockEntry(BaseModel):
    """Something in the kitchen. Names are unique per stock (case-insensitive): a new entry replaces an old one."""

    id: int
    name: str
    qty: str | None = None
    added: datetime.date
    """Purchase (or addition) date, local to the household."""
    source: Source = Source.MANUAL
    list_id: int | None = None
    list_index: int | None = None
    keeps: int | None = None
    due: datetime.date | None = None
    """Date to ask "still there?" (perishables only)."""
    asked: datetime.date | None = None
    """When "still there?" was asked; unanswered past the grace period, the entry leaves the stock."""
    boxes: bool = False
    """Batch-cooked dish stored in boxes: ``qty`` counts the boxes."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measure(self) -> Measure | None:
        """``qty`` read as a structured quantity, when it is one of the known forms (derived, never stored)."""
        quantity = parse(self.qty)
        return quantity.measure() if quantity else None


class Kitchen(BaseModel):
    """Everything the rules need for one household, including the id counters (ids are never reused)."""

    current_list: ShoppingList | None = None
    stock: list[StockEntry] = Field(default_factory=list)
    next_list_id: int = 1
    next_stock_id: int = 1


# --- inputs ---------------------------------------------------------------------------------------------------


class NewItem(BaseModel):
    """An item to put on a list."""

    model_config = _INPUT

    name: str
    qty: str | None = Field(default=None, validation_alias=_QTY)
    aisle: str | None = None
    keeps: KeepsValue = Field(default=None, validation_alias=_KEEPS)


class StockAdd(BaseModel):
    """Something to add to the stock (same name: replaces the entry, or adds to it with ``merge``)."""

    model_config = _INPUT

    name: str
    qty: str | None = Field(default=None, validation_alias=_QTY)
    keeps: KeepsValue = Field(default=None, validation_alias=_KEEPS)
    boxes: bool = False
    merge: bool = False


class StockRemove(BaseModel):
    """Something finished, thrown away or used.

    Without a quantity the entry leaves the stock. ``qty_left`` says what is left (the quantity is replaced);
    ``qty_used`` says what was used (subtracted from the entry, which leaves the stock when nothing is left).
    """

    model_config = _INPUT

    name: str
    qty_left: str | None = None
    qty_used: str | None = Field(default=None, validation_alias=AliasChoices("qty_used", "used"))


# --- results --------------------------------------------------------------------------------------------------


class EditSummary(BaseModel):
    """What an edit by name did, with the names as they appear on the list."""

    checked: list[str] = Field(default_factory=list)
    unchecked: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)


class StockSummary(BaseModel):
    """What a stock update did. Labels read "Rice (1 kg)", as Bob reported them to its language model."""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
