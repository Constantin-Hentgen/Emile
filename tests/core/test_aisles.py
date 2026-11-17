import datetime

from emile.core import Kitchen, NewItem, aisles, render, shopping

NOW = datetime.datetime(2026, 11, 17, 9, tzinfo=datetime.UTC)
TODAY = NOW.date()


def _list():
    kitchen = Kitchen()
    items = [
        NewItem(name=n, aisle=a) for n, a in [("Pain", "Bakery"), ("Lait", "Dairy"), ("Riz", "Dry"), ("Pâtes", "Dry")]
    ]
    return kitchen, shopping.new_list(kitchen, items, now=NOW, shop="Lidl")


def test_first_tick_of_each_aisle_is_positioned_among_the_aisles():
    kitchen, lst = _list()
    seen = {}
    for index in (2, 3, 1):  # Dry first, Dry again (not a first tick), Dairy second
        before = lst.model_copy(deep=True)
        shopping.toggle(kitchen, lst.id, index, today=TODAY)
        seen |= aisles.first_ticks(before, kitchen.current_list)
    assert seen == {"dry": 0.0, "dairy": 0.5}


def test_another_list_teaches_nothing():
    __, lst = _list()
    other = lst.model_copy(update={"id": lst.id + 1})
    assert aisles.first_ticks(lst, other) == {}
    assert aisles.first_ticks(None, lst) == {}


def test_scores_are_smoothed_and_only_trusted_after_two_trips():
    score, seen = aisles.blend(0.0, 0, 1.0)
    assert (score, seen) == (1.0, 1)
    assert aisles.order({"dry": (score, seen)}) == {}
    score, seen = aisles.blend(score, seen, 0.0)
    assert (score, seen) == (0.5, 2)
    assert aisles.order({"dry": (score, seen)}) == {"dry": 0.5}
    for __ in range(30):
        score, seen = aisles.blend(score, seen, 0.0)
    assert seen == aisles.MEMORY and score < 0.05


def test_known_aisles_come_first_in_shop_order():
    __, lst = _list()
    groups = render.group_by_aisle(lst.items, "en", {"dry": 0.1, "bakery": 0.9})
    assert list(groups) == ["Dry", "Bakery", "Dairy"]
    assert list(render.group_by_aisle(lst.items, "en")) == ["Bakery", "Dairy", "Dry"]
