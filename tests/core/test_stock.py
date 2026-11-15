import datetime

import pytest

from emile.core import Kitchen, LimitExceeded, Limits, Source, StockAdd, StockRemove, stock
from tests.core.conftest import TODAY

D = datetime.timedelta


def names(kitchen: Kitchen) -> list[str]:
    return [e.name for e in kitchen.stock]


def test_put_gives_ids_dates_and_due_dates(kitchen):
    rice = stock.put(kitchen, "Riz", "1 kg", TODAY)
    fish = stock.put(kitchen, "Cabillaud", "600 g", TODAY, keeps=2)
    assert (rice.id, rice.added, rice.due, rice.source) == (1, TODAY, None, Source.MANUAL)
    assert (fish.id, fish.keeps, fish.due) == (2, 2, TODAY + D(2))
    assert kitchen.next_stock_id == 3


def test_same_name_replaces_the_entry_case_insensitively(kitchen):
    stock.put(kitchen, "Riz", "1 kg", TODAY - D(5))
    stock.put(kitchen, "Pain", None, TODAY)
    again = stock.put(kitchen, "  riz ", "500 g", TODAY)
    assert names(kitchen) == ["Pain", "riz"]
    assert (again.id, again.qty, again.added) == (3, "500 g", TODAY)


def test_put_needs_a_name(kitchen):
    with pytest.raises(ValueError, match="name"):
        stock.put(kitchen, "  ", None, TODAY)


def test_stock_limit_counts_after_replacement(kitchen):
    limits = Limits(max_stock_items=2)
    stock.put(kitchen, "Riz", None, TODAY, limits=limits)
    stock.put(kitchen, "Pain", None, TODAY, limits=limits)
    stock.put(kitchen, "RIZ", "2 kg", TODAY, limits=limits)  # replacing never overflows
    with pytest.raises(LimitExceeded, match="stock full"):
        stock.put(kitchen, "Œufs", None, TODAY, limits=limits)
    assert names(kitchen) == ["Pain", "RIZ"]


def test_update_adds_removes_partially_and_reports(kitchen):
    """Port of Bob's test_update_stock_tool_add_remove_partial_and_report."""
    initial = [StockAdd(name="Riz", qty="1 kg"), StockAdd(name="Œufs", qty="6"), StockAdd(name="Cabillaud")]
    stock.update(kitchen, initial, today=TODAY)
    out = stock.update(
        kitchen,
        add=[StockAdd(name="Pain")],
        remove=[StockRemove(name="cabillaud"), StockRemove(name="Œufs", qty_left="2"), StockRemove(name="Pâtes")],
        today=TODAY,
    )
    assert out.model_dump() == {
        "added": ["Pain"],
        "removed": ["Cabillaud"],
        "updated": ["Œufs (2)"],
        "not_found": ["Pâtes"],
    }
    assert {e.name: e.qty for e in kitchen.stock} == {"Riz": "1 kg", "Œufs": "2", "Pain": None}


def test_update_removes_before_adding_and_accepts_bob_field_names(kitchen):
    stock.put(kitchen, "Riz", "100 g", TODAY - D(10))
    add = StockAdd.model_validate({"name": "Riz", "quantity": "2 kg", "keeps_days": 30})
    out = stock.update(kitchen, add=[add], remove=[StockRemove(name="riz")], today=TODAY)
    assert out.removed == ["Riz"] and out.added == ["Riz (2 kg)"]
    assert [(e.qty, e.added, e.due) for e in kitchen.stock] == [("2 kg", TODAY, TODAY + D(30))]


def test_update_skips_nameless_additions(kitchen):
    out = stock.update(kitchen, add=[StockAdd(name=" ")], today=TODAY)
    assert out.added == [] and kitchen.stock == []


def test_update_is_all_or_nothing(kitchen):
    limits = Limits(max_stock_items=2)
    stock.put(kitchen, "Riz", None, TODAY, limits=limits)
    before = kitchen.model_copy(deep=True)
    with pytest.raises(LimitExceeded):
        stock.update(
            kitchen,
            add=[StockAdd(name="Pain"), StockAdd(name="Œufs")],
            remove=[StockRemove(name="Riz", qty_left="un fond")],
            today=TODAY,
            limits=limits,
        )
    assert kitchen == before


def test_boxes_are_counted_and_shrink(kitchen):
    """Port of Bob's test_boxes_are_counted_from_cooked_dishes_and_shrink_when_eaten (stock part)."""
    stock.update(kitchen, [StockAdd(name="Poulet"), StockAdd(name="Riz", qty="1 kg")], today=TODAY)
    assert stock.free_boxes(kitchen.stock, 3) == 3
    stock.update(kitchen, [StockAdd(name="Curry de poulet", qty="2 boîtes", boxes=True)], today=TODAY)
    assert stock.boxes_in_use(kitchen.stock) == [("Curry de poulet", 2)]
    stock.update(kitchen, remove=[StockRemove(name="Curry de poulet", qty_left="1 boîte")], today=TODAY)
    stock.update(kitchen, [StockAdd(name="Chili", qty="2 boîtes", boxes=True)], today=TODAY)
    assert stock.boxes_in_use(kitchen.stock) == [("Curry de poulet", 1), ("Chili", 2)]
    assert stock.free_boxes(kitchen.stock, 3) == 0
    assert stock.free_boxes(kitchen.stock, 2) == 0  # never negative


@pytest.mark.parametrize(("qty", "n"), [(None, 1), ("3 tups", 3), ("  2 boxes", 2), ("une boîte", 1), ("12", 12)])
def test_box_count(qty, n):
    assert stock.box_count(qty) == n


# --- "still there?" ---------------------------------------------------------------------------------------------


@pytest.fixture
def perishables(kitchen) -> Kitchen:
    """Bought three days ago (Bob's test_daily_pantry_check_asks_then_expires)."""
    bought = TODAY - D(2)
    stock.update(
        kitchen,
        [StockAdd(name="Saumon", qty="300 g", keeps=2), StockAdd(name="Brocoli", keeps=2), StockAdd(name="Riz"),
         StockAdd(name="Yaourts", keeps=10)],
        today=bought,
    )  # fmt: skip
    return kitchen


def ids(kitchen: Kitchen) -> dict[str, int]:
    return {e.name: e.id for e in kitchen.stock}


def test_to_check_lists_due_perishables_not_asked_yet(perishables):
    assert [e.name for e in stock.to_check(perishables, TODAY)] == ["Saumon", "Brocoli"]
    assert stock.to_check(perishables, TODAY - D(1)) == []
    marked = stock.mark_asked(perishables, [ids(perishables)["Saumon"]], TODAY)
    assert [e.name for e in marked] == ["Saumon"] and marked[0].asked == TODAY
    assert [e.name for e in stock.to_check(perishables, TODAY + D(1))] == ["Brocoli"]  # asked once only


def test_still_there_pushes_the_date_back_by_half_the_keeping_time(perishables):
    yogurt = ids(perishables)["Yaourts"]
    stock.mark_asked(perishables, [yogurt], TODAY)
    entry = stock.answer(perishables, yogurt, still_there=True, today=TODAY)
    assert entry is not None and (entry.due, entry.asked) == (TODAY + D(5), None)
    salmon = stock.answer(perishables, ids(perishables)["Saumon"], still_there=True, today=TODAY)
    assert salmon is not None and salmon.due == TODAY + D(1)  # at least one day


def test_still_there_without_keeping_time_waits_a_day(kitchen):
    entry = stock.put(kitchen, "Restes", None, TODAY)
    assert stock.answer(kitchen, entry.id, still_there=True, today=TODAY).due == TODAY + D(1)


def test_finished_removes_and_a_second_answer_is_harmless(perishables):
    broccoli = ids(perishables)["Brocoli"]
    gone = stock.answer(perishables, broccoli, still_there=False, today=TODAY)
    assert gone is not None and gone.name == "Brocoli" and "Brocoli" not in names(perishables)
    assert stock.answer(perishables, broccoli, still_there=False, today=TODAY) is None


def test_unanswered_questions_expire_after_the_grace_period(perishables):
    stock.mark_asked(perishables, [ids(perishables)["Saumon"]], TODAY)
    assert stock.expire(perishables, TODAY + D(2)) == []
    gone = stock.expire(perishables, TODAY + D(3))
    assert [e.name for e in gone] == ["Saumon"]
    assert names(perishables) == ["Brocoli", "Riz", "Yaourts"]
    stock.mark_asked(perishables, [ids(perishables)["Brocoli"]], TODAY)
    assert [e.name for e in stock.expire(perishables, TODAY, Limits(ask_grace_days=0))] == ["Brocoli"]


def test_remove_ids(kitchen):
    a = stock.put(kitchen, "A", None, TODAY)
    stock.put(kitchen, "B", None, TODAY)
    assert [e.name for e in stock.remove_ids(kitchen, [a.id, 999])] == ["A"]
    assert names(kitchen) == ["B"]
