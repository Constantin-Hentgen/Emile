def test_export_holds_the_household_data_and_no_secrets(api):
    api.post("/v1/lists", json={"items": [{"name": "Lait", "qty": "1 L"}]})
    api.post("/v1/stock", json={"add": [{"name": "Riz", "qty": "1 kg"}]})
    api.post("/v1/shop-links", json={"label": "phone"})
    body = api.get("/v1/export").json()
    assert (body["format"], body["version"]) == ("emile-export", 1)
    assert set(body["household"]) == {"name", "timezone", "language", "currency", "meal_boxes"}
    assert [i["name"] for i in body["kitchen"]["current_list"]["items"]] == ["Lait"]
    assert [e["name"] for e in body["kitchen"]["stock"]] == ["Riz"]
    assert "shop_" not in str(body) and "emile_" not in str(body)


def test_export_needs_a_key(client):
    assert client.get("/v1/export").status_code == 401
