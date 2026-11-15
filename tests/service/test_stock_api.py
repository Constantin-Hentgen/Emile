"""Stock endpoints: Bob's update_stock tool, /stock command and daily "still there?" job."""


def names(api) -> list[str]:
    return [e["name"] for e in api.get("/v1/stock").json()["entries"]]


def ids(api) -> dict[str, int]:
    return {e["name"]: e["id"] for e in api.get("/v1/stock").json()["entries"]}


def test_update_stock_like_bobs_tool(api):
    api.post(
        "/v1/stock", json={"add": [{"name": "Riz", "qty": "1 kg"}, {"name": "Œufs", "qty": "6"}, {"name": "Cabillaud"}]}
    )
    res = api.post(
        "/v1/stock",
        json={
            "add": [{"name": "Pain"}],
            "remove": [{"name": "cabillaud"}, {"name": "Œufs", "qty_left": "2"}, {"name": "Pâtes"}],
        },
    )
    assert res.status_code == 200
    assert res.json() == {"added": ["Pain"], "removed": ["Cabillaud"], "updated": ["Œufs (2)"], "not_found": ["Pâtes"]}
    assert {e["name"]: e["qty"] for e in api.get("/v1/stock").json()["entries"]} == {
        "Riz": "1 kg",
        "Œufs": "2",
        "Pain": None,
    }


def test_boxes_in_the_stock_response(api):
    api.post("/v1/stock", json={"add": [{"name": "Chili", "qty": "2 boîtes", "boxes": True}, {"name": "Riz"}]})
    assert api.get("/v1/stock").json()["boxes"] == {
        "total": 3,
        "used": 2,
        "free": 1,
        "dishes": [{"name": "Chili", "boxes": 2}],
    }
    assert api.get("/v1/stock/text", params={"style": "boxes"}).text == "2/3 occupées (Chili ×2) → 1 libre(s)"
    api.patch("/v1/household", json={"meal_boxes": 6})
    assert api.get("/v1/stock").json()["boxes"]["free"] == 4


def test_stock_text(api, clock):
    api.post("/v1/stock", json={"add": [{"name": "Cabillaud", "qty": "600 g", "keeps_days": 2}]})
    clock.advance(days=1)
    api.post("/v1/stock", json={"add": [{"name": "Pain"}]})
    assert (
        api.get("/v1/stock/text").text
        == "- Cabillaud (600 g) · il y a 1 j — se garde jusqu'au mer. 11/11\n- Pain · aujourd'hui"
    )
    assert api.get("/v1/stock/text", params={"style": "prompt", "lang": "en"}).text.startswith(
        "- Cabillaud (600 g) — bought 1 d ago — keeps until Wed 11 Nov"
    )


def test_still_there_flow(api, clock):
    """Bob's daily job: expire, list what is due, ask, mark asked; answers come from buttons."""
    api.post(
        "/v1/stock",
        json={
            "add": [
                {"name": "Saumon", "qty": "300 g", "keeps_days": 2},
                {"name": "Brocoli", "keeps_days": 2},
                {"name": "Riz"},
                {"name": "Yaourts", "keeps_days": 10},
            ]
        },
    )
    entries = ids(api)
    assert api.get("/v1/stock/to-check").json() == {"date": "2026-11-09", "entries": []}
    clock.advance(days=3)
    due = api.get("/v1/stock/to-check").json()
    assert due["date"] == "2026-11-12"
    assert [e["name"] for e in due["entries"]] == ["Saumon", "Brocoli"]
    assert [e["name"] for e in api.get("/v1/stock/to-check", params={"date": "2026-11-10"}).json()["entries"]] == []

    asked = api.post("/v1/stock/asked", json={"ids": [entries["Saumon"], entries["Brocoli"]]}).json()
    assert [e["asked"] for e in asked["entries"]] == ["2026-11-12", "2026-11-12"]
    assert api.get("/v1/stock/to-check").json()["entries"] == []

    res = api.post(f"/v1/stock/{entries['Saumon']}/answer", json={"still_there": True}).json()
    assert (res["removed"], res["entry"]["due"], res["entry"]["asked"]) == (False, "2026-11-13", None)
    res = api.post(f"/v1/stock/{entries['Brocoli']}/answer", json={"still_there": False}).json()
    assert res["removed"] is True
    again = api.post(f"/v1/stock/{entries['Brocoli']}/answer", json={"still_there": False})
    assert again.status_code == 404  # "already removed"

    api.post("/v1/stock/asked", json={"ids": [entries["Saumon"]]})
    clock.advance(days=2)
    assert api.post("/v1/stock/expire").json()["removed"] == []
    clock.advance(days=1)
    expired = api.post("/v1/stock/expire").json()
    assert (expired["date"], [e["name"] for e in expired["removed"]]) == ("2026-11-15", ["Saumon"])
    assert names(api) == ["Riz", "Yaourts"]


def test_stock_limit_is_all_or_nothing(api, app):
    app.state.settings.max_stock_items = 2
    api.post("/v1/stock", json={"add": [{"name": "Riz"}]})
    res = api.post("/v1/stock", json={"add": [{"name": "Pain"}, {"name": "Œufs"}]})
    assert (res.status_code, res.json()["error"]) == (422, "limit_exceeded")
    assert names(api) == ["Riz"]
