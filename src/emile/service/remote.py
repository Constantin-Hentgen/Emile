"""The calendar server Emile syncs with, behind a small interface.

:class:`CalendarRemote` is what the sync needs; :class:`CaldavRemote` implements it with the ``caldav`` library.
Tests use an in-memory fake, and an optional integration test runs the same sync against a real Radicale.
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from emile.core import ical
from emile.core.tasks import RemoteTask


class RemoteError(Exception):
    """The calendar server refused or failed; the message never contains the password."""


@dataclass(frozen=True)
class RemoteEvent:
    uid: str
    summary: str
    description: str
    start: datetime.datetime
    end: datetime.datetime
    sequence: int = 0


class CalendarRemote(Protocol):
    def check(self) -> None:
        """Raise :class:`RemoteError` unless the calendar can be reached with these credentials."""

    def get(self, uid: str) -> RemoteEvent | None:
        """The event with this UID, or ``None``."""

    def put(self, uid: str, ics: str) -> None:
        """Create the event, or replace it if the UID exists."""

    def shopping_events(self) -> list[RemoteEvent]:
        """Every event written by Emile or by the client it takes over from (see :func:`ical.is_shopping_uid`)."""

    def delete(self, uid: str) -> None: ...

    def tasks(self) -> list[RemoteTask]:
        """Every task (completed ones included) written by Emile or the client it takes over from."""

    def put_task(self, uid: str, ics: str) -> None:
        """Create the task, or replace it if the UID exists."""

    def delete_task(self, uid: str) -> None: ...


type RemoteFactory = Callable[[str, str, str], CalendarRemote]
"""``(url, username, password) -> remote``; the app holds one, tests replace it."""


def _aware(value: datetime.datetime | datetime.date) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    return datetime.datetime.combine(value, datetime.time(), tzinfo=datetime.UTC)


def parse_event(data: str) -> RemoteEvent | None:
    """The first VEVENT of an iCalendar text, or ``None`` if there is none or it is not usable."""
    from icalendar import Calendar

    try:
        for comp in Calendar.from_ical(data).walk("VEVENT"):
            start = _aware(comp.decoded("DTSTART"))
            end = comp.get("DTEND")
            stop = _aware(end.dt) if end is not None else start + _duration(comp)
            return RemoteEvent(
                uid=str(comp.get("UID", "")),
                summary=str(comp.get("SUMMARY", "")),
                description=str(comp.get("DESCRIPTION", "")),
                start=start,
                end=stop,
                sequence=int(comp.get("SEQUENCE", 0) or 0),
            )
    except (ValueError, KeyError, TypeError):
        return None
    return None


def parse_task(data: str) -> RemoteTask | None:
    """The first VTODO of an iCalendar text, or ``None``."""
    from icalendar import Calendar

    try:
        for comp in Calendar.from_ical(data).walk("VTODO"):
            categories = comp.get("CATEGORIES")
            names = [str(c) for c in categories.cats] if categories is not None else []
            status = str(comp.get("STATUS", "")).upper()
            marked = comp.get("COMPLETED") is not None or comp.get("PERCENT-COMPLETE") == 100
            done = status == "COMPLETED" or (not status and marked)  # an explicit STATUS wins over the other hints
            return RemoteTask(
                uid=str(comp.get("UID", "")),
                summary=str(comp.get("SUMMARY", "")),
                category=names[0] if names else None,
                done=done,
                sequence=int(comp.get("SEQUENCE", 0) or 0),
            )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


def _duration(comp: Any) -> datetime.timedelta:
    duration = comp.get("DURATION")
    return duration.dt if duration is not None else datetime.timedelta()


class CaldavRemote:
    """A CalDAV calendar collection (e.g. a Radicale or Nextcloud calendar URL)."""

    def __init__(self, url: str, username: str, password: str, *, timeout: float = 15.0) -> None:
        import caldav

        client_class: Any = caldav.DAVClient
        self._url = url
        try:
            self._client = client_class(
                url=url,
                username=username or None,
                password=password or None,
                timeout=int(timeout),
                require_tls=False,  # a calendar on the home network is often plain http
                enable_rfc6764=False,  # never look anything up in DNS on behalf of a URL
            )
            self._calendar = self._client.calendar(url=url)
        except Exception as e:
            raise RemoteError(_describe(e)) from None

    def check(self) -> None:
        try:
            self._calendar.get_properties([_display_name()])
        except Exception as e:
            raise RemoteError(_describe(e)) from None

    def _object(self, uid: str) -> Any:
        try:
            return self._calendar.get_event_by_uid(uid)
        except Exception as e:
            if type(e).__name__ == "NotFoundError":
                return None
            raise RemoteError(_describe(e)) from None

    def get(self, uid: str) -> RemoteEvent | None:
        obj = self._object(uid)
        return parse_event(obj.data) if obj is not None else None

    def put(self, uid: str, ics: str) -> None:
        try:
            self._calendar.save_event(ics)
        except Exception as e:
            raise RemoteError(_describe(e)) from None

    def shopping_events(self) -> list[RemoteEvent]:
        found: list[RemoteEvent] = []
        try:
            for obj in self._calendar.get_events():
                event = parse_event(obj.data)
                if event is not None and ical.is_shopping_uid(event.uid):
                    found.append(event)
        except Exception as e:
            raise RemoteError(_describe(e)) from None
        return found

    def delete(self, uid: str) -> None:
        obj = self._object(uid)
        if obj is None:
            return
        try:
            obj.delete()
        except Exception as e:
            raise RemoteError(_describe(e)) from None

    def _todo(self, uid: str) -> Any:
        try:
            return self._calendar.get_todo_by_uid(uid)
        except Exception as e:
            if type(e).__name__ == "NotFoundError":
                return None
            raise RemoteError(_describe(e)) from None

    def tasks(self) -> list[RemoteTask]:
        found: list[RemoteTask] = []
        try:
            for obj in self._calendar.get_todos(include_completed=True):
                task = parse_task(obj.data)
                if task is not None and ical.is_shopping_uid(task.uid):
                    found.append(task)
        except Exception as e:
            raise RemoteError(_describe(e)) from None
        return found

    def put_task(self, uid: str, ics: str) -> None:
        try:
            self._calendar.save_todo(ics)
        except Exception as e:
            raise RemoteError(_describe(e)) from None

    def delete_task(self, uid: str) -> None:
        obj = self._todo(uid)
        if obj is None:
            return
        try:
            obj.delete()
        except Exception as e:
            raise RemoteError(_describe(e)) from None


def _display_name() -> Any:
    from caldav.elements import dav

    return dav.DisplayName()


def _describe(error: Exception) -> str:
    """A short message for the person fixing their setup; library errors can echo URLs, never secrets."""
    return f"{type(error).__name__}: {str(error)[:300]}"


def caldav_factory(url: str, username: str, password: str) -> CalendarRemote:
    return CaldavRemote(url, username, password)
