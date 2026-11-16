"""iCalendar text for the shopping event, built without any library and without any network.

The service writes it to a CalDAV server; keeping the rendering here makes it deterministic and testable against
fixtures. Times are written in UTC (``...Z``) so no VTIMEZONE is needed and every client understands them.
"""

import datetime
import uuid

UID_SUFFIX = "@emile-shopping"
"""Suffix of the events Emile creates: it recognises its own entries in a calendar by it, never by title."""

LEGACY_UID_SUFFIXES = ("@running-coach-shopping",)
"""Suffixes of events written by earlier clients (Bob, when it was still called Running Coach): Emile takes them
over when it starts syncing the same calendar, so the shopping list is not duplicated."""

PRODID = "-//emile//groceries//EN"


def new_uid() -> str:
    return f"{uuid.uuid4()}{UID_SUFFIX}"


def is_shopping_uid(uid: str) -> bool:
    """Whether an event was written by Emile or by the client it takes over from (never the user's own events)."""
    return uid.endswith((UID_SUFFIX, *LEGACY_UID_SUFFIXES))


def escape(value: str) -> str:
    """Escape a TEXT value (RFC 5545 §3.3.11)."""
    return (
        value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")
    )


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def fold(line: str) -> str:
    """Fold a content line at 75 octets, never inside a UTF-8 character (RFC 5545 §3.1)."""
    parts: list[str] = []
    current = ""
    size = 0
    limit = 75
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            parts.append(current)
            current, size, limit = " ", 1, 75  # continuation lines start with one space, counted in the limit
        current += char
        size += width
    parts.append(current)
    return "\r\n".join(parts)


def utc_stamp(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


def build_event(
    uid: str,
    summary: str,
    description: str,
    start: datetime.datetime,
    end: datetime.datetime,
    *,
    now: datetime.datetime,
    sequence: int = 0,
) -> str:
    """A calendar holding one VEVENT. ``start``, ``end`` and ``now`` must be timezone-aware."""
    if start.tzinfo is None or end.tzinfo is None or now.tzinfo is None:
        raise ValueError("event times must be timezone-aware")
    if end <= start:
        raise ValueError("the event must end after it starts")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{utc_stamp(now)}",
        f"DTSTART:{utc_stamp(start)}",
        f"DTEND:{utc_stamp(end)}",
        f"SUMMARY:{escape(summary)}",
        f"DESCRIPTION:{escape(description)}",
        f"SEQUENCE:{sequence}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
