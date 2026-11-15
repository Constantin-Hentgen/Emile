"""Request and response bodies of the HTTP API (``/v1``), shared by the service and the Python client.

Only pydantic is needed here, so a client can import these models without the web or database stack. Request
models accept the argument names of Bob's language-model tools (``days_covered``, ``estimated_cost_min_eur``,
``quantity``, ``keeps_days``) as aliases, so a tool call can be forwarded to the API unchanged.
"""

import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from emile.core.models import EditSummary, NewItem, ShoppingList, StockAdd, StockEntry, StockRemove, StockSummary

type Language = Literal["fr", "en"]

_INPUT = ConfigDict(populate_by_name=True, extra="ignore")


def _check_timezone(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown IANA time zone: {value!r}") from None
    return value


# --- operations -----------------------------------------------------------------------------------------------


class Health(BaseModel):
    """Liveness and readiness: the process answers and the database is reachable."""

    status: Literal["ok", "error"]
    version: str
    database: Literal["ok", "error"]


class ErrorBody(BaseModel):
    """Every error response: a stable machine-readable ``error`` code and a human ``detail``."""

    error: str
    detail: str


# --- accounts -------------------------------------------------------------------------------------------------


class Member(BaseModel):
    id: int
    name: str


class HouseholdOut(BaseModel):
    """A household: one shopping list and one stock, shared by its members."""

    id: int
    name: str
    timezone: str
    """IANA time zone: "today", due dates and "still there?" checks are local to it."""
    language: Language
    """Language of the texts Emile writes (checklists, calendar descriptions)."""
    currency: str
    meal_boxes: int
    """Meal-prep boxes the household owns, to tell how many are free."""
    members: list[Member] = Field(default_factory=list)


class HouseholdPatch(BaseModel):
    """Fields to change; omitted fields stay as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    timezone: str | None = None
    language: Language | None = None
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    meal_boxes: int | None = Field(default=None, ge=0, le=1000)

    _tz = field_validator("timezone")(_check_timezone)


class HouseholdCreate(BaseModel):
    """Admin: a new household. Unset fields take the deployment defaults."""

    name: str = Field(min_length=1, max_length=80)
    timezone: str | None = None
    language: Language | None = None
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    meal_boxes: int = Field(default=0, ge=0, le=1000)
    members: list[int] = Field(default_factory=list)
    """User ids."""

    _tz = field_validator("timezone")(_check_timezone)


class Me(BaseModel):
    id: int
    name: str
    households: list[HouseholdOut]


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class UserOut(BaseModel):
    id: int
    name: str
    created: datetime.datetime


class KeyCreate(BaseModel):
    label: str | None = Field(default=None, max_length=80)


class KeyCreated(BaseModel):
    """A new API key. ``key`` is shown this once: only its hash is stored."""

    id: int
    user_id: int
    label: str | None
    prefix: str
    key: str


class MemberAdd(BaseModel):
    user_id: int


# --- shopping list --------------------------------------------------------------------------------------------


class NewListRequest(BaseModel):
    """A new current list (replaces the previous one). Same arguments as Bob's ``save_shopping_list`` tool."""

    model_config = _INPUT

    items: list[NewItem]
    days: int | None = Field(default=None, validation_alias=AliasChoices("days", "days_covered"))
    coverage: str | None = None
    cost_min: float | None = Field(default=None, validation_alias=AliasChoices("cost_min", "estimated_cost_min_eur"))
    cost_max: float | None = Field(default=None, validation_alias=AliasChoices("cost_max", "estimated_cost_max_eur"))


class EditListRequest(BaseModel):
    """Edit by name; same arguments as Bob's ``edit_shopping_list`` tool."""

    model_config = _INPUT

    check: list[str] = Field(default_factory=list)
    uncheck: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    add: list[NewItem] = Field(default_factory=list)


class EditListResponse(BaseModel):
    """The edited list (with its new id) and what changed."""

    model_config = ConfigDict(populate_by_name=True)

    shopping_list: ShoppingList = Field(alias="list")
    summary: EditSummary


class RecipesRequest(BaseModel):
    text: str | None = None
    meals: list[int] = Field(default_factory=list)
    """The client's meal ids for these recipes (e.g. its "cooked" buttons)."""


# --- stock ----------------------------------------------------------------------------------------------------


class BoxDish(BaseModel):
    name: str
    boxes: int


class Boxes(BaseModel):
    """Meal-prep boxes: how many the household owns, how many hold a dish, how many are free."""

    total: int
    used: int
    free: int
    dishes: list[BoxDish]


class StockOut(BaseModel):
    entries: list[StockEntry]
    boxes: Boxes


class StockUpdateRequest(BaseModel):
    """Same arguments as Bob's ``update_stock`` tool."""

    model_config = _INPUT

    add: list[StockAdd] = Field(default_factory=list)
    remove: list[StockRemove] = Field(default_factory=list)


StockUpdateResponse = StockSummary


class ToCheck(BaseModel):
    """Perishables to ask "still there?" about, as of ``date``."""

    date: datetime.date
    entries: list[StockEntry]


class AskedRequest(BaseModel):
    ids: list[int]
    date: datetime.date | None = None
    """Defaults to today in the household's time zone."""


class AskedResponse(BaseModel):
    date: datetime.date
    entries: list[StockEntry]


class AnswerRequest(BaseModel):
    still_there: bool
    date: datetime.date | None = None


class AnswerResponse(BaseModel):
    """The entry after the answer; ``removed`` when the answer was "finished"."""

    entry: StockEntry
    removed: bool


class ExpireRequest(BaseModel):
    date: datetime.date | None = None


class ExpireResponse(BaseModel):
    """Entries removed because their "still there?" question went unanswered."""

    date: datetime.date
    removed: list[StockEntry]


__all__ = [
    "AnswerRequest",
    "AnswerResponse",
    "AskedRequest",
    "AskedResponse",
    "BoxDish",
    "Boxes",
    "EditListRequest",
    "EditListResponse",
    "ErrorBody",
    "ExpireRequest",
    "ExpireResponse",
    "Health",
    "HouseholdCreate",
    "HouseholdOut",
    "HouseholdPatch",
    "KeyCreate",
    "KeyCreated",
    "Language",
    "Me",
    "Member",
    "MemberAdd",
    "NewListRequest",
    "RecipesRequest",
    "ShoppingList",
    "StockAdd",
    "StockOut",
    "StockRemove",
    "StockUpdateRequest",
    "StockUpdateResponse",
    "ToCheck",
    "UserCreate",
    "UserOut",
]
