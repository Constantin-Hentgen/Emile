"""An in-memory CalDAV server for tests: calendars by URL, events stored as iCalendar text."""

import datetime

from emile.core import ical
from emile.service.remote import CalendarRemote, RemoteError, RemoteEvent, parse_event


class FakeServer:
    def __init__(self) -> None:
        self.calendars: dict[str, dict[str, str]] = {}  # url -> uid -> ics
        self.passwords: dict[str, str] = {}  # url -> password (the only user is "me")
        self.fail = False  # every call fails, like a server that is down
        self.drop_writes = False  # accepts a write but does not keep it: the read-back must catch it
        self.calls: list[str] = []

    def add_calendar(self, url: str, password: str = "secret") -> dict[str, str]:
        self.passwords[url] = password
        return self.calendars.setdefault(url, {})

    def add_foreign_event(self, url: str, uid: str, start: datetime.datetime, summary: str = "Dentist") -> None:
        """An entry that belongs to the household, not to Emile."""
        now = start - datetime.timedelta(days=30)
        self.calendars[url][uid] = ical.build_event(
            uid, summary, "mine", start, start + datetime.timedelta(hours=1), now=now
        )

    def connect(self, url: str, username: str, password: str) -> CalendarRemote:
        return FakeRemote(self, url, username, password)

    def uids(self, url: str) -> set[str]:
        return set(self.calendars[url])

    def event(self, url: str, uid: str) -> RemoteEvent:
        event = parse_event(self.calendars[url][uid])
        assert event is not None
        return event


class FakeRemote:
    def __init__(self, server: FakeServer, url: str, username: str, password: str) -> None:
        self.server, self.url, self.username, self.password = server, url, username, password

    def _store(self) -> dict[str, str]:
        if self.server.fail:
            raise RemoteError("ConnectionError: the server is down")
        if self.url not in self.server.calendars:
            raise RemoteError("NotFoundError: no such calendar")
        if self.server.passwords[self.url] != self.password:
            raise RemoteError("AuthorizationError: wrong password")
        return self.server.calendars[self.url]

    def check(self) -> None:
        self.server.calls.append("check")
        self._store()

    def get(self, uid: str) -> RemoteEvent | None:
        self.server.calls.append("get")
        ics = self._store().get(uid)
        return parse_event(ics) if ics else None

    def put(self, uid: str, ics: str) -> None:
        self.server.calls.append("put")
        store = self._store()
        if not self.server.drop_writes:
            store[uid] = ics

    def shopping_events(self) -> list[RemoteEvent]:
        self.server.calls.append("list")
        events = (parse_event(ics) for ics in self._store().values())
        return [e for e in events if e is not None and ical.is_shopping_uid(e.uid)]

    def delete(self, uid: str) -> None:
        self.server.calls.append("delete")
        self._store().pop(uid, None)
