"""The shopping event as text: iCalendar, title and description in fr/en, and the shopping slot of a list."""

import datetime

import pytest

from emile.core import InvalidInput, Kitchen, NewItem, ical, render, shopping
from tests.core.conftest import NOW

D = datetime.timedelta
START = datetime.datetime(2026, 11, 14, 9, 30, tzinfo=datetime.UTC)
END = START + D(hours=1)


def make_list(kitchen: Kitchen, **kwargs):
    return shopping.new_list(
        kitchen,
        [
            NewItem(name="Bananes", qty="7", aisle="🥬 Fruits"),
            NewItem(name="Lait", aisle="🥛 Crèmerie"),
            NewItem(name="Pommes", aisle="🥬 Fruits"),
            NewItem(name="Sel"),
        ],
        now=NOW,
        days=7,
        coverage="petits-déjeuners, dîners",
        cost_min=55,
        cost_max=70,
        **kwargs,
    )


# --- iCalendar --------------------------------------------------------------------------------------------------


def test_event_is_exact_icalendar_text():
    ics = ical.build_event(
        "abc@emile-shopping",
        "🛒 Courses (2 articles)",
        "Couvre : 7 jours\n\n☐ Pain, beurre; sel",
        START,
        END,
        now=NOW,
    )
    assert ics == (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//emile//groceries//EN\r\nBEGIN:VEVENT\r\n"
        "UID:abc@emile-shopping\r\nDTSTAMP:20261109T180000Z\r\nDTSTART:20261114T093000Z\r\n"
        "DTEND:20261114T103000Z\r\nSUMMARY:🛒 Courses (2 articles)\r\n"
        "DESCRIPTION:Couvre : 7 jours\\n\\n☐ Pain\\, beurre\\; sel\r\nSEQUENCE:0\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def test_text_escaping_round_trips():
    raw = "a, b; c\\d\nsecond line\r\nthird"
    assert ical.unescape(ical.escape(raw)) == "a, b; c\\d\nsecond line\nthird"


def test_long_lines_fold_at_75_octets_without_splitting_a_character():
    ics = ical.build_event("u@emile-shopping", "S", "é" * 200 + "\n" + "x" * 200, START, END, now=NOW)
    for line in ics.split("\r\n"):
        assert len(line.encode()) <= 75
    unfolded = ics.replace("\r\n ", "")
    assert "DESCRIPTION:" + "é" * 200 + "\\n" + "x" * 200 in unfolded


def test_event_times_must_be_aware_and_ordered():
    with pytest.raises(ValueError, match="timezone-aware"):
        ical.build_event("u", "S", "d", START.replace(tzinfo=None), END, now=NOW)
    with pytest.raises(ValueError, match="after it starts"):
        ical.build_event("u", "S", "d", END, START, now=NOW)


def test_emile_recognises_its_own_and_bobs_events_only():
    assert ical.is_shopping_uid(ical.new_uid())
    assert ical.is_shopping_uid("1234@running-coach-shopping")  # Bob's shopping events
    assert not ical.is_shopping_uid("1234@running-coach")  # Bob's training events are not Emile's business
    assert not ical.is_shopping_uid("birthday-party@example.org")


# --- title and description --------------------------------------------------------------------------------------


def test_event_text_in_french(kitchen):
    lst = make_list(kitchen)
    lst.items[1].done = True
    assert render.event_summary(lst, "fr") == "🛒 Courses (4 articles, 7 jours · 55–70 € estimés)"
    assert render.event_description(lst, "fr") == (
        "Couvre : 7 jours, petits-déjeuners, dîners\nCoût estimé : 55–70 €\n\n"
        "🥬 Fruits\n☐ Bananes — 7\n☐ Pommes\n\n🥛 Crèmerie\n✅ Lait\n\n🛒 Divers\n☐ Sel"
    )


def test_event_text_in_english_with_recipes(kitchen):
    lst = make_list(kitchen)
    shopping.set_recipes(kitchen, lst.id, "Banana bread: mash, mix, bake.")
    assert render.event_summary(lst, "en", "$") == "🛒 Shopping (4 items, 7 days · $55–70 estimated)"
    description = render.event_description(lst, "en", "$")
    assert description.startswith("Covers: 7 days, petits-déjeuners, dîners\nEstimated cost: $55–70\n\n🥬 Fruits")
    assert description.endswith("🛒 Misc\n☐ Sel\n\n🍳 RECIPES\n\nBanana bread: mash, mix, bake.")


def test_event_description_without_coverage_or_cost(kitchen):
    lst = shopping.new_list(kitchen, [NewItem(name="Riz")], now=NOW)
    assert render.event_description(lst, "fr") == "🛒 Divers\n☐ Riz"
    assert render.event_summary(lst, "fr") == "🛒 Courses (1 articles)"


# --- the shopping slot ------------------------------------------------------------------------------------------


def test_new_list_takes_a_slot(kitchen):
    lst = make_list(kitchen, start=START, end=END)
    assert (lst.event_start, lst.event_end, lst.event_uid) == (START, END, None)


def test_replacing_a_list_keeps_the_upcoming_slot_but_not_the_event(kitchen):
    first = make_list(kitchen, start=START, end=END)
    shopping.attach_event(kitchen, first.id, "old@emile-shopping", START, END)
    second = shopping.new_list(kitchen, [NewItem(name="Riz")], now=NOW)
    assert (second.event_start, second.event_end) == (START, END)
    assert second.event_uid is None  # the new list gets its own event; the sync removes the old one


def test_a_slot_that_has_passed_is_not_reused(kitchen):
    make_list(kitchen, start=START, end=END)
    later = START + D(days=3)
    lst = shopping.new_list(kitchen, [NewItem(name="Riz")], now=later)
    assert (lst.event_start, lst.event_end) == (None, None)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (START, None, "both a start and an end"),
        (START.replace(tzinfo=None), END, "timezone-aware"),
        (END, START, "after it starts"),
        (NOW - D(days=2), NOW - D(days=1), "in the past"),
    ],
)
def test_invalid_slots_are_refused(kitchen, start, end, message):
    with pytest.raises(InvalidInput, match=message):
        make_list(kitchen, start=start, end=end)
    assert kitchen.current_list is None


def test_editing_by_name_keeps_the_event_and_its_slot(kitchen):
    lst = make_list(kitchen, start=START, end=END)
    shopping.attach_event(kitchen, lst.id, "u@emile-shopping", START, END)
    edited, __ = shopping.edit(kitchen, check=["lait"], today=NOW.date())
    assert edited.id != lst.id
    assert (edited.event_uid, edited.event_start, edited.event_end) == ("u@emile-shopping", START, END)


def test_set_slot_moves_the_trip_and_keeps_the_event(kitchen):
    lst = make_list(kitchen, start=START, end=END)
    shopping.attach_event(kitchen, lst.id, "u@emile-shopping", START, END)
    moved = shopping.set_slot(kitchen, lst.id, START + D(days=1), END + D(days=1, hours=1), now=NOW)
    assert (moved.event_uid, moved.event_start, moved.event_end) == (
        "u@emile-shopping",
        START + D(days=1),
        END + D(days=1, hours=1),
    )
    with pytest.raises(InvalidInput, match="in the past"):
        shopping.set_slot(kitchen, lst.id, NOW - D(days=2), NOW - D(days=1), now=NOW)
