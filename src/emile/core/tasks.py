"""The shopping list as one task per item (VTODO), kept in step in both directions with a calendar of tasks.

Pure rules, no network: given the list, the tasks found in the calendar and what was true at the last sync,
:func:`plan` says what to tick locally, what to write to the calendar and what to delete there.

**Conflict rule.** Each item is compared with its task *and* with the state recorded at the last sync. The side
that changed since then wins; as an item is either bought or not, both sides can only have changed to the same
value. When nothing is recorded (first sync, or the record was lost) and the two disagree, **Emile wins**: the
list is the source of truth and a purchase is only ever recorded by an explicit tick.
"""

import datetime
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from emile.core import ical
from emile.core.models import ListItem, ShoppingList
from emile.core.render import item_label
from emile.core.text import name_key


@dataclass(frozen=True)
class RemoteTask:
    """A task as read from the calendar."""

    uid: str
    summary: str
    category: str | None
    done: bool
    sequence: int = 0


def list_token(lst: ShoppingList) -> str:
    """What identifies the list across edits (its calendar UID), from which every task UID derives."""
    return lst.event_uid or f"list-{lst.id}"


def item_uids(lst: ShoppingList, token: str) -> list[str]:
    """A stable UID per item: the same list and item name give the same UID, whatever the position or the list id
    (an edit by name moves items and renumbers the list but keeps its tasks)."""
    seen: dict[str, int] = {}
    out = []
    for item in lst.items:
        key = name_key(item.name)
        seen[key] = seen.get(key, 0) + 1
        out.append(f"{uuid.uuid5(uuid.NAMESPACE_URL, f'{token}/{key}/{seen[key]}')}{ical.UID_SUFFIX}")
    return out


def wanted(item: ListItem) -> tuple[str, str | None]:
    """The text the task must carry: its title and its category (the aisle)."""
    return item_label(item), item.aisle


@dataclass
class Plan:
    pull: dict[str, bool] = field(default_factory=dict)
    """Task UID → new state, for items ticked or unticked in the calendar."""
    push: list[str] = field(default_factory=list)
    """UIDs of the tasks to create or rewrite from the list."""
    delete: list[str] = field(default_factory=list)
    """UIDs of tasks that no longer belong to the list."""
    state: dict[str, bool] = field(default_factory=dict)
    """UID → bought, as it will be once the plan is applied: the record for the next sync."""


def plan(lst: ShoppingList, token: str, remote: Mapping[str, RemoteTask], last: Mapping[str, bool]) -> Plan:
    result = Plan()
    uids = item_uids(lst, token)
    for item, uid in zip(lst.items, uids, strict=True):
        task = remote.get(uid)
        done = item.done
        if task is None:  # new item, or the task was deleted in the calendar: the list decides, it is written again
            result.push.append(uid)
        elif task.done != item.done:
            if uid in last and last[uid] == item.done:  # only the calendar changed
                result.pull[uid] = done = task.done
            else:  # only the list changed, or there is no record: Emile wins
                result.push.append(uid)
        elif (task.summary, task.category) != wanted(item):
            result.push.append(uid)
        result.state[uid] = done
    result.delete = [uid for uid in remote if uid not in set(uids)]
    return result


def build_todo(
    uid: str, summary: str, category: str | None, done: bool, *, now: datetime.datetime, sequence: int = 0
) -> str:
    """A calendar holding one VTODO."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{ical.PRODID}",
        "BEGIN:VTODO",
        f"UID:{uid}",
        f"DTSTAMP:{ical.utc_stamp(now)}",
        f"SUMMARY:{ical.escape(summary)}",
    ]
    if category:
        lines.append(f"CATEGORIES:{ical.escape(category)}")
    lines.append(f"STATUS:{'COMPLETED' if done else 'NEEDS-ACTION'}")
    if done:
        lines += [f"COMPLETED:{ical.utc_stamp(now)}", "PERCENT-COMPLETE:100"]
    lines += [f"SEQUENCE:{sequence}", "END:VTODO", "END:VCALENDAR"]
    return "\r\n".join(ical.fold(line) for line in lines) + "\r\n"
