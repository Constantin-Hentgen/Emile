"""Shopping list endpoints, driven the way Bob uses them (tool arguments forwarded as-is, position buttons)."""

from tests.service.conftest import NOW, TODAY

BOB_SAVE = {  # arguments of Bob's save_shopping_list tool
    "days_covered": 7,
    "coverage": "déjeuners et dîners",
    "estimated_cost_min_eur": 55,
    "estimated_cost_max_eur": 70,
    "start_time": "2026-11-14T15:00:00",  # calendar slot: handled by the CalDAV sync (M4), ignored here
    "items": [
        {"name": "Bananes", "quantity": "7", "aisle": "🥦 Fruits", "keeps_days": 6},
        {"name": "Riz", "quantity": "1 kg", "aisle": "🍚 Féculents"},
        {"name": "Saumon", "quantity": "300 g", "aisle": "🐟 Poisson", "keeps_days": 2},
    ],
}


def stock_names(api) -> list[str]:
    return [e["name"] for e in api.get("/v1/stock").json()["entries"]]


def test_no_current_list_is_a_404(api):
    res = api.get("/v1/lists/current")
    assert res.status_code == 404
    assert res.json()["error"] == "no_current_list"


def test_create_from_bob_arguments_and_read_back(api):
    res = api.post("/v1/lists", json=BOB_SAVE)
    assert res.status_code == 201
    lst = res.json()
    assert (lst["id"], lst["days"], lst["cost_min"], lst["cost_max"]) == (1, 7, 55, 70)
    assert [(i["name"], i["qty"], i["keeps"], i["done"]) for i in lst["items"]] == [
        ("Bananes", "7", 6, False), ("Riz", "1 kg", None, False), ("Saumon", "300 g", 2, False)
    ]  # fmt: skip
    assert lst["created"].startswith("2026-11-09T17:00:00")
    assert api.get("/v1/lists/current").json() == lst


def test_toggle_puts_items_in_stock_and_stale_ids_are_refused(api):
    lst = api.post("/v1/lists", json=BOB_SAVE).json()
    res = api.post(f"/v1/lists/{lst['id']}/items/2/toggle")
    assert res.status_code == 200
    assert res.json()["items"][2]["done"] is True
    salmon = api.get("/v1/stock").json()["entries"][0]
    assert (salmon["name"], salmon["source"], salmon["added"], salmon["due"]) == (
        "Saumon",
        "list",
        "2026-11-09",
        "2026-11-11",
    )

    edited = api.patch("/v1/lists/current", json={"check": ["bananes"]}).json()
    assert edited["list"]["id"] == 2
    res = api.post(f"/v1/lists/{lst['id']}/items/0/toggle")  # an old button
    assert res.status_code == 409
    assert res.json()["error"] == "stale_list"
    assert api.post("/v1/lists/2/items/9/toggle").status_code == 404


def test_edit_reports_like_bobs_tool(api):
    api.post("/v1/lists", json=BOB_SAVE)
    res = api.patch(
        "/v1/lists/current",
        json={"check": ["riz"], "remove": ["Saumon"], "add": [{"name": "Pain", "quantity": "1"}], "uncheck": ["Pâtes"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["summary"] == {"checked": ["Riz"], "unchecked": [], "removed": ["Saumon"], "added": ["Pain"],
                               "not_found": ["Pâtes"]}  # fmt: skip
    assert [i["name"] for i in body["list"]["items"]] == ["Bananes", "Riz", "Pain"]
    assert stock_names(api) == ["Riz"]


def test_reset_and_recipes(api):
    lst = api.post("/v1/lists", json=BOB_SAVE).json()
    api.post(f"/v1/lists/{lst['id']}/items/0/toggle")
    api.post("/v1/stock", json={"add": [{"name": "Œufs", "qty": "6"}]})
    res = api.post(f"/v1/lists/{lst['id']}/reset")
    assert not any(i["done"] for i in res.json()["items"])
    assert stock_names(api) == ["Œufs"]
    res = api.put(f"/v1/lists/{lst['id']}/recipes", json={"text": "Riz au saumon", "meals": [12, 13]})
    assert (res.json()["recipes"], res.json()["recipe_meals"]) == ("Riz au saumon", [12, 13])
    assert api.put("/v1/lists/99/recipes", json={"text": "x"}).status_code == 409


def test_text_renderings_in_household_language_or_requested_one(api):
    lst = api.post("/v1/lists", json=BOB_SAVE).json()
    api.post(f"/v1/lists/{lst['id']}/items/0/toggle")
    res = api.get("/v1/lists/current/text")
    assert res.headers["content-type"].startswith("text/plain")
    assert res.text.startswith("🥦 Fruits\n✅ Bananes — 7\n\n🍚 Féculents\n☐ Riz — 1 kg")
    assert api.get("/v1/lists/current/text", params={"style": "summary"}).text == "7 jours · 55–70 € estimés"
    assert (
        api.get("/v1/lists/current/text", params={"style": "summary", "lang": "en"}).text == "7 days · €55–70 estimated"
    )
    assert api.get("/v1/lists/current/text", params={"style": "prompt"}).text.startswith("(3 articles, 1 achetés")
    assert api.get("/v1/lists/current/text", params={"style": "nope"}).status_code == 422


def test_limits_and_invalid_input(api, app):
    app.state.settings.max_list_items = 2
    res = api.post("/v1/lists", json=BOB_SAVE)
    assert res.status_code == 422
    assert res.json()["error"] == "limit_exceeded"
    res = api.post("/v1/lists", json={"items": [{"name": "  "}]})
    assert (res.status_code, res.json()["error"]) == (422, "invalid_input")
    res = api.post("/v1/lists", json={"items": "bananas"})
    assert (res.status_code, res.json()["error"]) == (422, "validation_error")


def test_household_members_share_one_list(api, make_account, alice):
    partner = make_account("partner", household=alice.household_id)
    lst = api.post("/v1/lists", json=BOB_SAVE).json()
    res = api.post(f"/v1/lists/{lst['id']}/items/1/toggle", headers=partner.headers)  # partner ticks in the shop
    assert res.status_code == 200
    assert api.get("/v1/lists/current").json()["items"][1]["done"] is True
    stranger = make_account("stranger")
    assert api.get("/v1/lists/current", headers=stranger.headers).status_code == 404  # own empty household


def test_today_is_local_to_the_household(api, clock):
    clock.now = NOW.replace(hour=23, minute=30)  # 00:30 on the 10th in Paris
    lst = api.post("/v1/lists", json=BOB_SAVE).json()
    api.post(f"/v1/lists/{lst['id']}/items/0/toggle")
    assert api.get("/v1/stock").json()["entries"][0]["added"] == "2026-11-10"
    assert TODAY.isoformat() == "2026-11-09"
