import datetime

import pytest

from emile.core import (
    ItemNotFound,
    Kitchen,
    LimitExceeded,
    Limits,
    NewItem,
    NoCurrentList,
    Source,
    StaleList,
    StockAdd,
    StockRemove,
    shopping,
    stock,
)
from tests.core.conftest import NOW, TODAY

D = datetime.timedelta


def items(*specs: str | tuple) -> list[NewItem]:
    """ "Riz" or ("Bananes", "7") or ("Saumon", "300 g", "🐟 Poisson", 2)."""
    out = []
    for spec in specs:
        name, qty, aisle, keeps = (spec, None, None, None) if isinstance(spec, str) else (*spec, None, None, None)[:4]
        out.append(NewItem(name=name, qty=qty, aisle=aisle, keeps=keeps))
    return out


def in_stock(kitchen: Kitchen) -> list[tuple]:
    return sorted((e.name, e.list_id, e.list_index) for e in kitchen.stock)


# --- new list -------------------------------------------------------------------------------------------------


def test_new_list_cleans_items_and_replaces_the_previous_one(kitchen):
    first = shopping.new_list(kitchen, items("Riz"), now=NOW)
    lst = shopping.new_list(
        kitchen,
        [NewItem(name="  Bananes ", qty=" 7 ", aisle="🥦 Fruits", keeps="6"), NewItem(name=" "), NewItem(name="Pain")],
        now=NOW,
        days="7",
        coverage="  déjeuners   et dîners ",
        cost_min=70,
        cost_max=55,
    )
    assert (first.id, lst.id, kitchen.current_list) == (1, 2, lst)
    assert [(i.name, i.qty, i.aisle, i.keeps, i.done) for i in lst.items] == [
        ("Bananes", "7", "🥦 Fruits", 6, False),
        ("Pain", None, None, None, False),
    ]
    assert (lst.days, lst.coverage, lst.cost_min, lst.cost_max, lst.created) == (7, "déjeuners et dîners", 55, 70, NOW)


def test_new_list_accepts_bob_tool_arguments(kitchen):
    raw = {"name": "Bananes", "quantity": "7", "aisle": "🥦 Fruits", "keeps_days": 6}
    lst = shopping.new_list(kitchen, [NewItem.model_validate(raw)], now=NOW)
    assert (lst.items[0].qty, lst.items[0].keeps) == ("7", 6)


def test_new_list_rejects_empty_and_oversized_lists(kitchen):
    with pytest.raises(ValueError, match="empty"):
        shopping.new_list(kitchen, items(" "), now=NOW)
    with pytest.raises(LimitExceeded, match="at most 2"):
        shopping.new_list(kitchen, items("A", "B", "C"), now=NOW, limits=Limits(max_list_items=2))
    assert kitchen.current_list is None and kitchen.next_list_id == 1


def test_previous_list_purchases_stay_in_stock(kitchen):
    lst = shopping.new_list(kitchen, items("Riz"), now=NOW)
    shopping.toggle(kitchen, lst.id, 0, today=TODAY)
    shopping.new_list(kitchen, items("Pain"), now=NOW)
    assert [e.name for e in kitchen.stock] == ["Riz"]


# --- toggle and reset -------------------------------------------------------------------------------------------


def test_ticked_items_enter_the_stock_and_unticked_leave(kitchen):
    """Port of Bob's test_ticked_items_enter_the_stock_and_unticked_leave."""
    lst = shopping.new_list(kitchen, items(("Bananes", "7"), ("Riz", "500 g")), now=NOW)
    shopping.toggle(kitchen, lst.id, 0, today=TODAY)
    shopping.toggle(kitchen, lst.id, 1, today=TODAY)
    assert [(e.name, e.qty, e.source) for e in kitchen.stock] == [
        ("Bananes", "7", Source.LIST),
        ("Riz", "500 g", Source.LIST),
    ]
    shopping.toggle(kitchen, lst.id, 1, today=TODAY)  # unticked: leaves the stock
    assert [e.name for e in kitchen.stock] == ["Bananes"]
    stock.update(kitchen, [StockAdd(name="Œufs", qty="6")], today=TODAY)
    shopping.reset(kitchen, lst.id)  # "untick all": removes what the list added, nothing else
    assert [e.name for e in kitchen.stock] == ["Œufs"]
    assert not any(i.done for i in kitchen.current_list.items)


def test_ticking_a_perishable_sets_its_due_date(kitchen):
    lst = shopping.new_list(kitchen, items(("Saumon", "300 g", "🐟", 2)), now=NOW)
    shopping.toggle(kitchen, lst.id, 0, today=TODAY)
    assert kitchen.stock[0].due == TODAY + D(2)


def test_toggle_refuses_stale_lists_and_bad_positions(kitchen):
    with pytest.raises(NoCurrentList):
        shopping.toggle(kitchen, 1, 0, today=TODAY)
    old = shopping.new_list(kitchen, items("Riz"), now=NOW)
    new = shopping.new_list(kitchen, items("Pain"), now=NOW)
    with pytest.raises(StaleList, match="no longer current"):
        shopping.toggle(kitchen, old.id, 0, today=TODAY)
    with pytest.raises(StaleList):
        shopping.reset(kitchen, old.id)
    with pytest.raises(ItemNotFound):
        shopping.toggle(kitchen, new.id, 1, today=TODAY)
    with pytest.raises(ItemNotFound):
        shopping.toggle(kitchen, new.id, -1, today=TODAY)
    assert kitchen.stock == []


def test_ticking_into_a_full_stock_changes_nothing(kitchen):
    limits = Limits(max_stock_items=1)
    stock.put(kitchen, "Riz", None, TODAY)
    lst = shopping.new_list(kitchen, items("Pain"), now=NOW)
    with pytest.raises(LimitExceeded):
        shopping.toggle(kitchen, lst.id, 0, today=TODAY, limits=limits)
    assert not lst.items[0].done and [e.name for e in kitchen.stock] == ["Riz"]


# --- edit by name ---------------------------------------------------------------------------------------------


def test_edit_checks_removes_adds_and_repoints_the_stock(kitchen):
    """Port of Bob's test_edit_shopping_list_checks_removes_adds_and_syncs_event (list and stock part)."""
    lst = shopping.new_list(kitchen, items(("Bananes", "7"), "Riz", ("Œufs", "6"), "Thon en boîte"), now=NOW, days=7)
    shopping.attach_event(kitchen, lst.id, "uid-courses")
    shopping.toggle(kitchen, lst.id, 2, today=TODAY - D(1))  # eggs bought yesterday
    new, out = shopping.edit(
        kitchen,
        check=["bananes", "Thon en boîte"],
        remove=["Riz"],
        add=[NewItem.model_validate({"name": "Pain", "quantity": "1"})],
        uncheck=["Pâtes"],
        today=TODAY,
    )
    assert [(i.name, i.done) for i in new.items] == [
        ("Bananes", True),
        ("Œufs", True),
        ("Thon en boîte", True),
        ("Pain", False),
    ]
    assert out.model_dump() == {
        "checked": ["Bananes", "Thon en boîte"], "unchecked": [], "removed": ["Riz"], "added": ["Pain"],
        "not_found": ["Pâtes"],
    }  # fmt: skip
    assert new.id != lst.id and (new.event_uid, new.days) == ("uid-courses", 7)
    # stock: ticked items once each, pointing at their new position; the purchase date is kept
    assert in_stock(kitchen) == [("Bananes", new.id, 0), ("Thon en boîte", new.id, 2), ("Œufs", new.id, 1)]
    assert next(e for e in kitchen.stock if e.name == "Œufs").added == TODAY - D(1)
    shopping.toggle(kitchen, new.id, 1, today=TODAY)  # untick the eggs on the new list: they leave
    assert "Œufs" not in [e.name for e in kitchen.stock]
    with pytest.raises(StaleList):  # old buttons do nothing
        shopping.toggle(kitchen, lst.id, 0, today=TODAY)


def test_edit_unchecking_and_removing_take_items_out_of_the_stock(kitchen):
    lst = shopping.new_list(kitchen, items("A", "B", "C"), now=NOW)
    for n in range(3):
        shopping.toggle(kitchen, lst.id, n, today=TODAY)
    stock.put(kitchen, "Sel", None, TODAY)
    new, __ = shopping.edit(kitchen, uncheck=["a"], remove=["B"], today=TODAY)
    assert in_stock(kitchen) == [("C", new.id, 1), ("Sel", None, None)]


def test_edit_does_not_bring_back_what_was_eaten(kitchen):
    """Deliberate fix vs Bob: an item ticked earlier but since removed from the stock stays out of it."""
    lst = shopping.new_list(kitchen, items("Bananes", "Riz"), now=NOW)
    shopping.toggle(kitchen, lst.id, 0, today=TODAY)
    stock.update(kitchen, remove=[StockRemove(name="Bananes")], today=TODAY)  # eaten
    new, __ = shopping.edit(kitchen, add=[NewItem(name="Pain")], today=TODAY)
    assert new.items[0].done and kitchen.stock == []
    # ...but ticking it again in an edit does count as a new purchase
    shopping.edit(kitchen, uncheck=["Bananes"], today=TODAY)
    shopping.edit(kitchen, check=["Bananes"], today=TODAY)
    assert [e.name for e in kitchen.stock] == ["Bananes"]


def test_edit_ignores_duplicate_and_nameless_additions(kitchen):
    shopping.new_list(kitchen, items("Riz"), now=NOW)
    new, out = shopping.edit(kitchen, add=[NewItem(name="RIZ"), NewItem(name=" "), NewItem(name="Pain")], today=TODAY)
    assert [i.name for i in new.items] == ["Riz", "Pain"] and out.added == ["Pain"] and out.not_found == []


def test_edit_keeps_recipes_cost_and_slot(kitchen):
    lst = shopping.new_list(kitchen, items("Riz"), now=NOW, cost_min=10, cost_max=12, coverage="dîners")
    start = NOW + D(days=1)
    shopping.attach_event(kitchen, lst.id, "uid", start, start + D(minutes=30))
    shopping.set_recipes(kitchen, lst.id, "  Riz cantonais  ", [4, 5])
    new, __ = shopping.edit(kitchen, check=["riz"], today=TODAY)
    assert (new.recipes, new.recipe_meals, new.event_start, new.cost_max, new.coverage, new.created) == (
        "Riz cantonais", [4, 5], start, 12, "dîners", NOW,
    )  # fmt: skip


def test_edit_needs_a_current_list(kitchen):
    with pytest.raises(NoCurrentList):
        shopping.edit(kitchen, check=["Riz"], today=TODAY)


def test_edit_over_the_limits_changes_nothing(kitchen):
    shopping.new_list(kitchen, items("A", "B"), now=NOW)
    before = kitchen.model_copy(deep=True)
    with pytest.raises(LimitExceeded, match="at most 2"):
        shopping.edit(kitchen, add=[NewItem(name="C")], today=TODAY, limits=Limits(max_list_items=2))
    with pytest.raises(LimitExceeded, match="stock full"):
        shopping.edit(kitchen, check=["A", "B"], today=TODAY, limits=Limits(max_stock_items=1))
    assert kitchen == before


def test_recipes_and_event_are_addressed_by_list_id(kitchen):
    lst = shopping.new_list(kitchen, items("Riz"), now=NOW)
    shopping.set_recipes(kitchen, lst.id, "   ")
    assert lst.recipes is None and lst.recipe_meals == []
    shopping.new_list(kitchen, items("Pain"), now=NOW)
    with pytest.raises(StaleList):
        shopping.set_recipes(kitchen, lst.id, "trop tard")
    with pytest.raises(StaleList):
        shopping.attach_event(kitchen, lst.id, "uid")


def test_kitchen_round_trips_through_json(kitchen):
    """Library users persist the kitchen themselves (a JSON file is enough): nothing may be lost on the way."""
    lst = shopping.new_list(kitchen, items(("Saumon", "300 g", "🐟", 2), "Riz"), now=NOW, cost_min=5.5)
    shopping.toggle(kitchen, lst.id, 0, today=TODAY)
    stock.update(kitchen, [StockAdd(name="Chili", qty="2 boîtes", boxes=True)], today=TODAY)
    assert Kitchen.model_validate_json(kitchen.model_dump_json()) == kitchen
