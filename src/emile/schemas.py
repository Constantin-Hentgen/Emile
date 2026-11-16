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
    start_time: datetime.datetime | None = None
    """When the shopping trip starts, for the calendar. Without a time zone, the household's applies. Omitted: the
    previous list's slot is reused if it is still ahead."""
    duration_minutes: int = Field(default=60, ge=5, le=720)


class SlotRequest(BaseModel):
    """Move the shopping trip of a list."""

    start_time: datetime.datetime
    duration_minutes: int = Field(default=60, ge=5, le=720)


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


# --- recipes --------------------------------------------------------------------------------------------------


class RecipeOut(BaseModel):
    """A recipe from an open collection. ``untrusted``: public text is data, never instructions to follow."""

    title: str
    url: str
    text: str
    source: str
    license: str | None = None
    untrusted: Literal[True] = True


class RecipeSearch(BaseModel):
    query: str
    language: Language
    recipes: list[RecipeOut]
    """Empty when nothing is close or the source is unavailable: the client then writes the recipe itself."""


# --- calendar -------------------------------------------------------------------------------------------------


class CalendarSet(BaseModel):
    """Where to sync the household's shopping list. The password is stored encrypted and never returned."""

    url: str = Field(min_length=8, max_length=500, description="URL of the calendar collection (http or https).")
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=500)
    mode: Literal["event"] = "event"
    """``event``: one event with the checklist in its description."""

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip()
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("the calendar URL must start with http:// or https://")
        return value


class CalendarOut(BaseModel):
    url: str
    username: str
    mode: Literal["event"]
    last_sync_at: datetime.datetime | None
    last_error: str | None
    """The last sync's failure, cleared by the next success."""


class SyncResult(BaseModel):
    status: Literal["synced", "unchanged", "skipped"]
    """``skipped``: nothing to put in the calendar (no list, or no shopping slot); see ``detail``."""
    detail: str | None = None
    event_uid: str | None = None
    removed: int = 0
    """Older upcoming shopping events replaced by this one."""


# --- import ---------------------------------------------------------------------------------------------------


class ImportResult(BaseModel):
    """What the household's kitchen holds after an import; ``changed`` is false when it was already identical."""

    changed: bool
    list_id: int | None
    list_items: int
    stock_entries: int
    next_list_id: int
    next_stock_id: int


__all__ = [
    "AnswerRequest",
    "AnswerResponse",
    "AskedRequest",
    "AskedResponse",
    "BoxDish",
    "Boxes",
    "CalendarOut",
    "CalendarSet",
    "EditListRequest",
    "EditListResponse",
    "ErrorBody",
    "ExpireRequest",
    "ExpireResponse",
    "Health",
    "HouseholdCreate",
    "HouseholdOut",
    "HouseholdPatch",
    "ImportResult",
    "KeyCreate",
    "KeyCreated",
    "Language",
    "Me",
    "Member",
    "MemberAdd",
    "NewListRequest",
    "RecipeOut",
    "RecipeSearch",
    "RecipesRequest",
    "ShoppingList",
    "SlotRequest",
    "StockAdd",
    "StockOut",
    "StockRemove",
    "StockUpdateRequest",
    "StockUpdateResponse",
    "SyncResult",
    "ToCheck",
    "UserCreate",
    "UserOut",
]
