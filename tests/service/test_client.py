"""The typed client, exercised against the real app."""

import datetime

import pytest

from emile.client import USER_AGENT, ApiError, Emile
from emile.core import NewItem, StockAdd


@pytest.fixture
def emile(client, alice):
    return Emile("http://testserver", alice.key, http=client)


def test_client_covers_the_bot_flows(emile, clock):
    assert emile.health().status == "ok"
    assert emile.me().name == "alice"
    assert emile.current_list() is None
    lst = emile.create_list([NewItem(name="Bananes", qty="7", keeps=6), {"name": "Riz", "quantity": "1 kg"}], days=7)
    lst = emile.toggle(lst.id, 0)
    assert lst.items[0].done
    new, summary = emile.edit_list(check=["riz"], add=[{"name": "Pain"}])
    assert (summary.checked, summary.added) == (["Riz"], ["Pain"])
    with pytest.raises(ApiError) as exc:
        emile.toggle(lst.id, 0)
    assert (exc.value.status, exc.value.error) == (409, "stale_list")
    assert emile.set_recipes(new.id, "Banana bread", [1]).recipes == "Banana bread"
    assert emile.list_text("summary", "en") == "7 days"
    assert emile.reset(new.id).items[1].done is False

    emile.update_stock(add=[StockAdd(name="Saumon", keeps=2), {"name": "Chili", "qty": "2", "boxes": True}])
    assert emile.stock().boxes.used == 2
    assert "Saumon" in emile.stock_text("prompt")
    clock.advance(days=2)
    due = emile.to_check()
    assert [e.name for e in due.entries] == ["Saumon"]
    emile.mark_asked([due.entries[0].id])
    assert emile.answer(due.entries[0].id, still_there=False).removed
    assert emile.answer(due.entries[0].id, still_there=False) is None
    assert emile.expire(datetime.date(2026, 12, 1)).removed == []
    assert emile.update_household(meal_boxes=5).meal_boxes == 5
    assert emile.household().meal_boxes == 5


def test_client_sends_a_generic_user_agent(client, alice):
    seen = {}
    original = client.request

    def spy(method, url, **kwargs):
        seen.update(kwargs["headers"])
        return original(method, url, **kwargs)

    client.request = spy
    Emile("http://testserver", alice.key, household=alice.household_id, http=client).stock()
    assert seen["User-Agent"] == USER_AGENT
    assert USER_AGENT.startswith("emile-client/")
    assert seen["Emile-Household"] == str(alice.household_id)


def test_client_errors_carry_the_api_code(client):
    with pytest.raises(ApiError) as exc:
        Emile("http://testserver", "emile_wrong", http=client).stock()
    assert (exc.value.status, exc.value.error) == (401, "unauthorized")
