"""Typed Python client for the Emile HTTP API (install with ``pip install emile[client]``).

Every method maps to one endpoint and returns the same pydantic models as the API::

    from emile.client import Emile

    with Emile("http://emile:8000", api_key) as emile:
        lst = emile.create_list([NewItem(name="Bananas", qty="7")], days=7)
        emile.toggle(lst.id, 0)

Errors raise :class:`ApiError` with the API's stable ``error`` code (``stale_list``, ``no_current_list``…).
"""

import datetime
from collections.abc import Sequence
from types import TracebackType
from typing import Any, Literal, Self

import httpx2
from pydantic import BaseModel, TypeAdapter

from emile import __version__
from emile.core.models import EditSummary, NewItem, ShoppingList, StockAdd, StockRemove, StockSummary
from emile.schemas import (
    AnswerResponse,
    AskedResponse,
    CalendarOut,
    EditListResponse,
    ExpireResponse,
    Health,
    HouseholdOut,
    ImportResult,
    Language,
    Me,
    RecipeSearch,
    StockOut,
    SyncResult,
    ToCheck,
)

# Generic on purpose: no user or host details leave through the User-Agent.
USER_AGENT = f"emile-client/{__version__}"


class ApiError(Exception):
    """An error response: ``status`` (HTTP), ``error`` (stable code) and ``detail`` (human text)."""

    def __init__(self, status: int, error: str, detail: Any) -> None:
        super().__init__(f"{status} {error}: {detail}")
        self.status = status
        self.error = error
        self.detail = detail


def _dump(items: Sequence[BaseModel | dict[str, Any]]) -> list[dict[str, Any]]:
    return [i.model_dump(exclude_none=True) if isinstance(i, BaseModel) else dict(i) for i in items]


class Emile:
    """Client bound to one API key (and optionally one household, for users in several)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        household: int | None = None,
        timeout: float = 10.0,
        http: httpx2.Client | None = None,
    ) -> None:
        """``http`` lets a caller supply its own client (connection pooling, proxies, or a test client)."""
        self._owns_http = http is None
        self._http = http or httpx2.Client(base_url=base_url, timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}
        if household is not None:
            self._headers["Emile-Household"] = str(household)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: tuple[type[BaseException] | None, BaseException | None, TracebackType | None]) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # --- plumbing ---------------------------------------------------------------------------------------------

    def _request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None
    ) -> httpx2.Response:
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        response = self._http.request(method, path, json=json, params=clean_params, headers=self._headers)
        if response.status_code >= 400:
            try:
                body = response.json()
                raise ApiError(response.status_code, body.get("error", "error"), body.get("detail"))
            except ValueError:
                raise ApiError(response.status_code, "error", response.text) from None
        return response

    def _get[M](self, model: type[M], method: str, path: str, **kwargs: Any) -> M:
        return TypeAdapter(model).validate_python(self._request(method, path, **kwargs).json())

    # --- account ----------------------------------------------------------------------------------------------

    def health(self) -> Health:
        return self._get(Health, "GET", "/health")

    def me(self) -> Me:
        return self._get(Me, "GET", "/v1/me")

    def household(self) -> HouseholdOut:
        return self._get(HouseholdOut, "GET", "/v1/household")

    def update_household(self, **fields: Any) -> HouseholdOut:
        """``name``, ``timezone``, ``language``, ``currency``, ``meal_boxes``."""
        return self._get(HouseholdOut, "PATCH", "/v1/household", json=fields)

    # --- shopping list ----------------------------------------------------------------------------------------

    def create_list(
        self,
        items: Sequence[NewItem | dict[str, Any]],
        *,
        days: int | None = None,
        coverage: str | None = None,
        cost_min: float | None = None,
        cost_max: float | None = None,
        start_time: datetime.datetime | None = None,
        duration_minutes: int | None = None,
    ) -> ShoppingList:
        """``start_time`` (without a time zone: the household's) is when the shopping trip starts, for the calendar."""
        body = {
            "items": _dump(items),
            "days": days,
            "coverage": coverage,
            "cost_min": cost_min,
            "cost_max": cost_max,
            "start_time": start_time and start_time.isoformat(),
            "duration_minutes": duration_minutes,
        }
        return self._get(ShoppingList, "POST", "/v1/lists", json={k: v for k, v in body.items() if v is not None})

    def current_list(self) -> ShoppingList | None:
        """The current list, or ``None`` if the household has none."""
        try:
            return self._get(ShoppingList, "GET", "/v1/lists/current")
        except ApiError as e:
            if e.error == "no_current_list":
                return None
            raise

    def edit_list(
        self,
        *,
        check: Sequence[str] = (),
        uncheck: Sequence[str] = (),
        remove: Sequence[str] = (),
        add: Sequence[NewItem | dict[str, Any]] = (),
    ) -> tuple[ShoppingList, EditSummary]:
        body = {"check": list(check), "uncheck": list(uncheck), "remove": list(remove), "add": _dump(add)}
        out = self._get(EditListResponse, "PATCH", "/v1/lists/current", json=body)
        return out.shopping_list, out.summary

    def toggle(self, list_id: int, index: int) -> ShoppingList:
        """Raises :class:`ApiError` ``stale_list`` (409) when ``list_id`` is no longer current."""
        return self._get(ShoppingList, "POST", f"/v1/lists/{list_id}/items/{index}/toggle")

    def reset(self, list_id: int) -> ShoppingList:
        return self._get(ShoppingList, "POST", f"/v1/lists/{list_id}/reset")

    def set_recipes(self, list_id: int, text: str | None, meals: Sequence[int] = ()) -> ShoppingList:
        return self._get(ShoppingList, "PUT", f"/v1/lists/{list_id}/recipes", json={"text": text, "meals": list(meals)})

    def set_slot(self, list_id: int, start_time: datetime.datetime, duration_minutes: int = 60) -> ShoppingList:
        """Move the shopping trip (the calendar event follows at the next sync)."""
        body = {"start_time": start_time.isoformat(), "duration_minutes": duration_minutes}
        return self._get(ShoppingList, "PUT", f"/v1/lists/{list_id}/slot", json=body)

    def list_text(
        self, style: Literal["checklist", "prompt", "summary"] = "checklist", lang: Language | None = None
    ) -> str:
        return self._request("GET", "/v1/lists/current/text", params={"style": style, "lang": lang}).text

    # --- stock ------------------------------------------------------------------------------------------------

    def stock(self) -> StockOut:
        return self._get(StockOut, "GET", "/v1/stock")

    def update_stock(
        self,
        add: Sequence[StockAdd | dict[str, Any]] = (),
        remove: Sequence[StockRemove | dict[str, Any]] = (),
    ) -> StockSummary:
        return self._get(StockSummary, "POST", "/v1/stock", json={"add": _dump(add), "remove": _dump(remove)})

    def stock_text(self, style: Literal["compact", "prompt", "boxes"] = "compact", lang: Language | None = None) -> str:
        return self._request("GET", "/v1/stock/text", params={"style": style, "lang": lang}).text

    def to_check(self, date: datetime.date | None = None) -> ToCheck:
        return self._get(ToCheck, "GET", "/v1/stock/to-check", params={"date": date and date.isoformat()})

    def mark_asked(self, ids: Sequence[int], date: datetime.date | None = None) -> AskedResponse:
        body = {"ids": list(ids), "date": date and date.isoformat()}
        return self._get(AskedResponse, "POST", "/v1/stock/asked", json=body)

    def answer(self, entry_id: int, still_there: bool, date: datetime.date | None = None) -> AnswerResponse | None:
        """``None`` when the entry is already gone (answered twice, or expired)."""
        body = {"still_there": still_there, "date": date and date.isoformat()}
        try:
            return self._get(AnswerResponse, "POST", f"/v1/stock/{entry_id}/answer", json=body)
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    def expire(self, date: datetime.date | None = None) -> ExpireResponse:
        return self._get(ExpireResponse, "POST", "/v1/stock/expire", json={"date": date and date.isoformat()})

    # --- calendar ---------------------------------------------------------------------------------------------

    def set_calendar(
        self, url: str, username: str = "", password: str = "", mode: Literal["event", "tasks"] = "event"
    ) -> CalendarOut:
        """Sync the household's shopping list to this CalDAV calendar (the connection is tried first).

        ``mode="event"`` writes one event; ``mode="tasks"`` writes one task per item, ticked in both directions.
        """
        body = {"url": url, "username": username, "password": password, "mode": mode}
        return self._get(CalendarOut, "PUT", "/v1/calendar", json=body)

    def calendar(self) -> CalendarOut | None:
        """The calendar settings and last sync state, or ``None`` if there is no calendar."""
        try:
            return self._get(CalendarOut, "GET", "/v1/calendar")
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    def remove_calendar(self) -> None:
        self._request("DELETE", "/v1/calendar")

    def sync(self) -> SyncResult:
        """Sync now (the server also does it every ``EMILE_SYNC_INTERVAL`` seconds)."""
        return self._get(SyncResult, "POST", "/v1/sync")

    # --- recipes and import -----------------------------------------------------------------------------------

    def search_recipes(self, query: str, lang: Language | None = None) -> RecipeSearch:
        """Recipes from open collections; the text is untrusted public content."""
        return self._get(RecipeSearch, "GET", "/v1/recipes/search", params={"q": query, "lang": lang})

    def import_bob(self, state: dict[str, Any], *, replace: bool = False) -> ImportResult:
        """Import the kitchen part of a Bob ``state.json`` (idempotent)."""
        return self._get(ImportResult, "POST", "/v1/import/bob", json=state, params={"replace": str(replace).lower()})
