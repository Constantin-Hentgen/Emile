def _trip(api, order):
    items = [{"name": n, "aisle": a} for n, a in [("Pain", "Boulangerie"), ("Lait", "Crèmerie"), ("Riz", "Épicerie")]]
    lst = api.post("/v1/lists", json={"items": items, "shop": "Lidl"}).json()
    index = {i["aisle"]: n for n, i in enumerate(lst["items"])}
    for aisle in order:
        api.post(f"/v1/lists/{lst['id']}/items/{index[aisle]}/toggle")
    return lst


def test_the_order_is_learned_over_two_trips_and_applied(api):
    _trip(api, ["Épicerie", "Crèmerie", "Boulangerie"])
    assert api.get("/v1/aisle-order", params={"shop": "lidl"}).json()["aisles"] == []
    _trip(api, ["Épicerie", "Crèmerie", "Boulangerie"])
    assert api.get("/v1/aisle-order", params={"shop": "LIDL"}).json()["aisles"] == [
        "épicerie",
        "crèmerie",
        "boulangerie",
    ]
    api.post(
        "/v1/lists",
        json={
            "items": [{"name": "Pain", "aisle": "Boulangerie"}, {"name": "Riz", "aisle": "Épicerie"}],
            "shop": "Lidl",
        },
    )
    text = api.get("/v1/lists/current/text").json() if False else api.get("/v1/lists/current/text").text
    assert text.index("Épicerie") < text.index("Boulangerie")


def test_a_list_without_a_shop_learns_nothing(api):
    lst = api.post("/v1/lists", json={"items": [{"name": "Riz", "aisle": "A"}, {"name": "Pain", "aisle": "B"}]}).json()
    api.post(f"/v1/lists/{lst['id']}/items/1/toggle")
    assert api.get("/v1/aisle-order", params={"shop": "Lidl"}).json()["aisles"] == []


def test_forgetting_a_shop(api):
    _trip(api, ["Épicerie", "Crèmerie", "Boulangerie"])
    _trip(api, ["Épicerie", "Crèmerie", "Boulangerie"])
    assert api.delete("/v1/aisle-order", params={"shop": "Lidl"}).status_code == 204
    assert api.get("/v1/aisle-order", params={"shop": "Lidl"}).json()["aisles"] == []
