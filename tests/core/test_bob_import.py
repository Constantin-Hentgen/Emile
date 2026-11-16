"""Conversion of a (synthetic) Bob state.json."""

import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from emile.core import InvalidInput, Kitchen, LimitExceeded, Limits, Source, bob, shopping
from tests.core.conftest import NOW, TODAY

STATE = json.loads((Path(__file__).parent.parent / "fixtures" / "bob_state.json").read_text(encoding="utf-8"))
PARIS = ZoneInfo("Europe/Paris")


def convert(state=STATE, **kw) -> Kitchen:
    return bob.kitchen_from_state(state, now=NOW, tz=PARIS, **kw)


def test_list_is_imported_with_its_id_and_details():
    lst = convert().current_list
    assert (lst.id, lst.days, lst.cost_min, lst.cost_max, lst.recipe_meals) == (7, 7, 55.0, 70.0, [31, 32])
    assert [(i.name, i.qty, i.keeps, i.done) for i in lst.items] == [
        ("Bananes", "7", 6, True), ("Riz basmati", "1 kg", None, False), ("Saumon", "300 g", 2, True),
        ("Yaourts nature", "8", 10, False),
    ]  # fmt: skip
    assert lst.event_uid.endswith("@running-coach-shopping")
    assert lst.event_start == datetime.datetime(2026, 11, 14, 14, 0, tzinfo=datetime.UTC)
    assert lst.created.isoformat() == "2026-11-08T17:02:11.123456+00:00"
    assert lst.recipes.startswith("*Riz au saumon*")


def test_stock_keeps_ids_dates_and_gets_structured_sources():
    kitchen = convert()
    by_name = {e.name: e for e in kitchen.stock}
    assert [e.id for e in kitchen.stock] == [9, 11, 12, 13, 14, 15]
    assert (by_name["Bananes"].source, by_name["Bananes"].list_id, by_name["Bananes"].list_index) == (Source.LIST, 7, 0)
    assert (by_name["Pâtes"].list_id, by_name["Pâtes"].list_index) == (5, 3)  # an older list: kept as history
    assert by_name["Œufs"].source == Source.MANUAL
    assert (by_name["Sel"].id, by_name["Sel"].source, by_name["Sel"].added) == (
        15,
        Source.IMPORT,
        datetime.date(2026, 10, 1),
    )
    assert by_name["Chili sin carne"].boxes is True
    saumon = by_name["Saumon"]
    assert (saumon.due, saumon.asked) == (datetime.date(2026, 11, 10), datetime.date(2026, 11, 10))
    assert (kitchen.next_list_id, kitchen.next_stock_id) == (8, 16)


def test_only_kitchen_keys_are_read():
    dumped = convert().model_dump_json()
    assert "SYNTHETIC" not in dumped


def test_imported_list_behaves_like_a_native_one():
    """Bob's buttons carry the list id and positions: unticking after the import removes the right stock entry."""
    kitchen = convert()
    shopping.toggle(kitchen, 7, 0, today=TODAY)
    assert "Bananes" not in [e.name for e in kitchen.stock]
    new, __ = shopping.edit(kitchen, check=["riz basmati"], today=TODAY)
    assert new.id == 8
    assert {e.name: (e.list_id, e.list_index) for e in kitchen.stock if e.list_id == 8} == {
        "Saumon": (8, 2), "Riz basmati": (8, 1),
    }  # fmt: skip


def test_conversion_is_deterministic_and_merge_is_idempotent():
    assert convert() == convert()
    kitchen = Kitchen(next_list_id=3, next_stock_id=40)
    assert bob.merge_into(kitchen, convert()) is True
    assert (kitchen.next_list_id, kitchen.next_stock_id) == (8, 40)  # counters never go backwards
    assert bob.merge_into(kitchen, convert()) is False


def test_partial_and_odd_states():
    only_stock = convert({"pantry": [{"name": "Riz"}, {"name": " riz ", "qty": "2 kg"}, {"name": ""}, "junk"]})
    assert only_stock.current_list is None
    assert [(e.id, e.name, e.qty, e.added) for e in only_stock.stock] == [
        (2, "riz", "2 kg", NOW.astimezone(PARIS).date())
    ]
    no_list = convert({"shopping": None, "pantry": []})
    assert no_list == Kitchen()
    naive = convert({"shopping": {"id": 2, "items": [{"name": "Riz"}], "created": "2026-11-08T18:00:00",
                                  "cost_min": "abc", "days": "x"}})  # fmt: skip
    assert naive.current_list.created == datetime.datetime(2026, 11, 8, 18, 0, tzinfo=PARIS)
    assert (naive.current_list.cost_min, naive.current_list.days, naive.next_list_id) == (None, None, 3)


def test_rejects_non_bob_files_and_oversized_data():
    with pytest.raises(InvalidInput, match="not a Bob state"):
        convert({"memories": []})
    with pytest.raises(LimitExceeded):
        convert(limits=Limits(max_list_items=3))
    with pytest.raises(LimitExceeded):
        convert(limits=Limits(max_stock_items=5))
