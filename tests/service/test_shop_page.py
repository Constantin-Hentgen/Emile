"""The shop page: a revocable link that can read the list and tick items, and nothing else."""

import pytest

from emile.client import ApiError, Emile
from emile.service.app import create_app
from emile.service.settings import Settings


@pytest.fixture
def link(api) -> dict:
    """A shop link for alice's household, and a list with two aisles."""
    api.post(
        "/v1/lists",
        json={
            "items": [
                {"name": "Bananes", "qty": "7", "aisle": "🥬 Fruits", "keeps": 6},
                {"name": "Riz", "qty": "1 kg", "aisle": "🛒 Épicerie"},
                {"name": "Sel"},
            ],
            "days": 7,
            "cost_min": 20,
            "cost_max": 25,
        },
    )
    res = api.post("/v1/shop-links", json={"label": "Anna's phone"})
    assert res.status_code == 201
    return res.json()


def shop(client, token, method="get", path="/v1/shop"):
    return getattr(client, method)(path, headers={"Authorization": f"Bearer {token}"})


def test_a_new_link_is_shown_once_and_points_at_the_fragment(link, api):
    assert link["token"].startswith("shop_")
    assert link["path"] == f"/shop#{link['token']}"
    listed = api.get("/v1/shop-links").json()
    assert [(row["id"], row["label"], row["revoked"]) for row in listed] == [(link["id"], "Anna's phone", False)]
    assert "token" not in listed[0]


def test_the_page_reads_the_list_grouped_by_aisle(client, link):
    view = shop(client, link["token"]).json()
    assert view["language"] == "fr"
    assert (view["total"], view["done"], view["summary"]) == (3, 0, "7 jours · 20–25 € estimés")
    assert [(g["aisle"], [i["label"] for i in g["items"]]) for g in view["groups"]] == [
        ("🥬 Fruits", ["Bananes — 7"]),
        ("🛒 Épicerie", ["Riz — 1 kg"]),
        ("🛒 Divers", ["Sel"]),
    ]


def test_ticking_buys_and_unticking_returns(client, api, link):
    token = link["token"]
    view = shop(client, token).json()
    path = f"/v1/shop/lists/{view['list_id']}/items/0/toggle"
    ticked = shop(client, token, "post", path).json()
    assert ticked["done"] == 1 and ticked["groups"][0]["items"][0]["done"] is True
    assert [e["name"] for e in api.get("/v1/stock").json()["entries"]] == ["Bananes"]
    assert shop(client, token, "post", path).json()["done"] == 0
    assert api.get("/v1/stock").json()["entries"] == []


def test_reset_unticks_everything(client, api, link):
    token = link["token"]
    list_id = shop(client, token).json()["list_id"]
    for index in (0, 1):
        shop(client, token, "post", f"/v1/shop/lists/{list_id}/items/{index}/toggle")
    assert len(api.get("/v1/stock").json()["entries"]) == 2
    assert shop(client, token, "post", f"/v1/shop/lists/{list_id}/reset").json()["done"] == 0
    assert api.get("/v1/stock").json()["entries"] == []


def test_a_replaced_list_is_refused_like_a_stale_button(client, api, link):
    token = link["token"]
    list_id = shop(client, token).json()["list_id"]
    api.patch("/v1/lists/current", json={"add": [{"name": "Pain"}]})
    res = shop(client, token, "post", f"/v1/shop/lists/{list_id}/items/0/toggle")
    assert (res.status_code, res.json()["error"]) == (409, "stale_list")


def test_no_list_is_a_404(client, api):
    token = api.post("/v1/shop-links", json={}).json()["token"]
    res = shop(client, token)
    assert (res.status_code, res.json()["error"]) == (404, "no_current_list")


def test_a_shop_link_can_do_nothing_else(client, link):
    headers = {"Authorization": f"Bearer {link['token']}"}
    for method, path in [
        ("get", "/v1/stock"),
        ("get", "/v1/lists/current"),
        ("post", "/v1/lists"),
        ("get", "/v1/me"),
        ("get", "/v1/shop-links"),
        ("put", "/v1/calendar"),
        ("post", "/v1/shop-links"),
    ]:
        res = getattr(client, method)(path, headers=headers)
        assert res.status_code == 401, (method, path)


def test_an_api_key_is_not_a_shop_link(client, alice):
    assert client.get("/v1/shop", headers=alice.headers).status_code == 401
    assert client.get("/v1/shop").status_code == 401
    assert shop(client, "shop_made-up").status_code == 401


def test_a_revoked_link_stops_working(client, api, link):
    assert api.delete(f"/v1/shop-links/{link['id']}").status_code == 204
    assert shop(client, link["token"]).status_code == 401
    assert api.get("/v1/shop-links").json()[0]["revoked"] is True


def test_links_belong_to_their_household(client, api, link, make_account):
    bob = make_account("bob")
    client.headers.update(bob.headers)
    assert client.get("/v1/shop-links").json() == []
    assert client.delete(f"/v1/shop-links/{link['id']}").status_code == 400
    client.post("/v1/lists", json={"items": [{"name": "Lait"}]})
    view = shop(client, link["token"]).json()  # alice's link still shows alice's list
    assert [i["label"] for g in view["groups"] for i in g["items"]] == ["Bananes — 7", "Riz — 1 kg", "Sel"]


def test_the_page_is_static_and_locked_down(client, link):
    res = client.get("/shop")
    assert res.status_code == 200
    assert "Bananes" not in res.text and link["token"] not in res.text
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert "connect-src 'self'" in res.headers["content-security-policy"]
    assert "default-src 'none'" in res.headers["content-security-policy"]
    assert "innerHTML" not in res.text  # item names are only ever inserted as text


def test_the_page_can_be_turned_off(settings, clock, make_account_for):
    from fastapi.testclient import TestClient

    off = create_app(
        Settings(_env_file=None, database_url=settings.database_url + "-off", shop_page=False), clock=clock
    )
    with TestClient(off) as client:
        account = make_account_for(off)
        assert client.get("/shop").status_code == 404
        assert client.get("/v1/shop", headers=account.headers).status_code == 404
        # managing links still works, so they are ready when the page is turned on
        assert client.post("/v1/shop-links", json={}, headers=account.headers).status_code == 201


def test_the_python_client(client, api, alice):
    emile = Emile("http://testserver", alice.key, http=client)
    created = emile.create_shop_link("kiosk")
    assert [link.label for link in emile.shop_links()] == ["kiosk"]
    emile.revoke_shop_link(created.id)
    assert emile.shop_links()[0].revoked
    with pytest.raises(ApiError) as exc:
        emile.revoke_shop_link(999)
    assert exc.value.error == "account_error"
