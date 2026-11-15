import datetime
from zoneinfo import ZoneInfo

import pytest

from emile.core import Kitchen, NewItem, StockAdd, render, shopping, stock
from emile.core.models import ListItem, ShoppingList, StockEntry
from tests.core.conftest import NOW, TODAY

D = datetime.timedelta


def lst(**fields) -> ShoppingList:
    return ShoppingList(id=1, created=NOW, **fields)


@pytest.mark.parametrize(
    ("fields", "fr", "en"),
    [
        ({"days": 7, "cost_min": 55, "cost_max": 70}, "7 jours · 55–70 € estimés", "7 days · €55–70 estimated"),
        ({"days": 1, "cost_min": 12.4}, "1 jour · ~12 € estimés", "1 day · ~€12 estimated"),
        ({"cost_min": 39.6, "cost_max": 40.2}, "~40 € estimés", "~€40 estimated"),
        ({}, "", ""),
    ],
)
def test_summary_line_and_cost(fields, fr, en):
    """Bob's test_shopping_summary_and_cost_formats, in both languages."""
    assert render.summary_line(lst(**fields), "fr") == fr
    assert render.summary_line(lst(**fields), "en") == en


def test_format_cost_other_currency():
    assert render.format_cost(None, 20, "en", currency="CHF") == "~CHF20"
    assert render.format_cost(None, None) is None


def test_checklist_groups_by_aisle_in_order_of_appearance():
    items = [
        ListItem(name="Bananes", qty="7", aisle="🥦 Fruits", done=True),
        ListItem(name="Riz", aisle="🍚 Féculents"),
        ListItem(name="Pommes", aisle="🥦 Fruits"),
        ListItem(name="Sel"),
    ]
    assert render.checklist(items, "fr") == (
        "🥦 Fruits\n✅ Bananes — 7\n☐ Pommes\n\n🍚 Féculents\n☐ Riz\n\n🛒 Divers\n☐ Sel"
    )
    groups = render.group_by_aisle(items, "en")
    assert list(groups) == ["🥦 Fruits", "🍚 Féculents", "🛒 Misc"]
    assert [n for n, __ in groups["🥦 Fruits"]] == [0, 2]  # positions, as toggling takes them


def test_list_text_for_a_prompt():
    tz = ZoneInfo("Europe/Paris")
    shopping_list = lst(
        days=7,
        items=[ListItem(name="Bananes", qty="7", aisle="🥦 Fruits", done=True), ListItem(name="Riz")],
        event_start=datetime.datetime(2026, 11, 14, 14, 0, tzinfo=datetime.UTC),
    )
    assert render.list_text(shopping_list, "fr", tz) == (
        "(2 articles, 1 achetés, couvre 7 jours, dans l'agenda le sam. 14/11 15:00) — ✅ = acheté:\n"
        "- ✅ Bananes (7) [🥦 Fruits]\n- ☐ Riz"
    )
    assert render.list_text(None, "en") == "none"
    assert render.list_text(lst(items=[ListItem(name="Riz")]), "en") == "(1 items, 0 bought) — ✅ = bought:\n- ☐ Riz"


def test_stock_text_shows_age_oldest_first():
    """Bob's test_pantry_context_shows_age_and_stock_reply."""
    entries = [
        StockEntry(id=2, name="Pain", added=TODAY),
        StockEntry(id=1, name="Cabillaud", qty="600 g", added=TODAY - D(3)),
    ]
    assert render.stock_text(entries, TODAY, "fr", compact=False) == (
        "- Cabillaud (600 g) — acheté il y a 3 j\n- Pain — acheté aujourd'hui"
    )
    assert render.stock_text(entries, TODAY, "fr") == "- Cabillaud (600 g) · il y a 3 j\n- Pain · aujourd'hui"
    assert render.stock_text(entries, TODAY, "en") == "- Cabillaud (600 g) · 3 d ago\n- Pain · today"
    assert render.stock_text([], TODAY, "fr") == "vide"


def test_due_labels():
    def entry(due, asked=None):
        return StockEntry(id=1, name="Saumon", added=TODAY - D(5), due=due, asked=asked)

    assert render.due_label(StockEntry(id=1, name="Riz", added=TODAY), TODAY) == ""
    assert render.due_label(entry(TODAY + D(7)), TODAY, "fr") == " — se garde jusqu'au lun. 16/11"
    assert render.due_label(entry(TODAY + D(7)), TODAY, "en") == " — keeps until Mon 16 Nov"
    assert render.due_label(entry(TODAY), TODAY, "fr") == " — à consommer aujourd'hui"
    assert render.due_label(entry(TODAY - D(1)), TODAY, "en") == " — past its date"
    assert "encore là" in render.due_label(entry(TODAY - D(1), asked=TODAY), TODAY, "fr")


def test_boxes_text():
    kitchen = Kitchen()
    assert render.boxes_text(kitchen.stock, 3, "fr") == "0/3 occupées → 3 libre(s)"
    stock.update(kitchen, [StockAdd(name="Curry de poulet", qty="2 boîtes", boxes=True)], today=TODAY)
    assert render.boxes_text(kitchen.stock, 3, "fr") == "2/3 occupées (Curry de poulet ×2) → 1 libre(s)"
    assert render.boxes_text(kitchen.stock, 1, "en") == "2/1 in use (Curry de poulet ×2) → 0 free"


def test_unknown_language_falls_back_to_english():
    kitchen = Kitchen()
    shopping.new_list(kitchen, [NewItem(name="Riz")], now=NOW)
    assert render.checklist(kitchen.current_list.items, "de") == "🛒 Misc\n☐ Riz"  # type: ignore[arg-type]
