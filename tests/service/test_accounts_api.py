"""Authentication, households, admin API, and the optimistic locking that protects concurrent writes."""

import datetime

import pytest
from sqlalchemy import select

from emile.core import StockAdd, stock
from emile.service import accounts, repository
from emile.service.tables import ApiKey
from tests.service.conftest import ADMIN_KEY, NOW


def test_requests_need_a_valid_key(client, alice):
    assert client.get("/v1/stock").status_code == 401
    res = client.get("/v1/stock", headers={"Authorization": "Bearer emile_nope"})
    assert (res.status_code, res.json()["error"]) == (401, "unauthorized")
    assert res.headers["www-authenticate"] == "Bearer"
    assert client.get("/v1/stock", headers=alice.headers).status_code == 200


def test_revoked_keys_stop_working_and_last_use_is_recorded(app, client, alice, clock):
    client.get("/v1/me", headers=alice.headers)
    with app.state.db.session() as session:
        key = session.scalar(select(ApiKey).where(ApiKey.user_id == alice.user_id))
        assert key.last_used_at == NOW
        assert key.key_hash != alice.key and alice.key not in key.prefix + key.key_hash  # never stored in clear
        accounts.revoke_key(session, key.id, NOW)
    assert client.get("/v1/me", headers=alice.headers).status_code == 401


def test_me_and_household_settings(api, alice):
    me = api.get("/v1/me").json()
    assert me["name"] == "alice"
    assert me["households"] == [
        {
            "id": alice.household_id,
            "name": "alice's",
            "timezone": "Europe/Paris",
            "language": "fr",
            "currency": "€",
            "meal_boxes": 3,
            "members": [{"id": alice.user_id, "name": "alice"}],
        }
    ]
    res = api.patch("/v1/household", json={"timezone": "America/Montreal", "language": "en", "meal_boxes": 5})
    assert (res.json()["timezone"], res.json()["language"], res.json()["meal_boxes"]) == ("America/Montreal", "en", 5)
    res = api.patch("/v1/household", json={"timezone": "Mars/Olympus"})
    assert (res.status_code, res.json()["error"]) == (422, "validation_error")
    assert api.get("/v1/household").json()["timezone"] == "America/Montreal"


def test_users_in_several_households_choose_one(app, client, make_account, alice):
    other = make_account("bob")
    with app.state.db.session() as session:  # alice also joins bob's household
        accounts.add_member(session, accounts.find_household(session, other.household_id),
                            accounts.find_user(session, "alice"), NOW)  # fmt: skip
    res = client.get("/v1/stock", headers=alice.headers)
    assert res.status_code == 400
    assert "Emile-Household" in res.json()["detail"]
    hh = {**alice.headers, "Emile-Household": str(other.household_id)}
    client.post("/v1/stock", json={"add": [{"name": "Riz"}]}, headers=hh)
    assert [e["name"] for e in client.get("/v1/stock", headers=other.headers).json()["entries"]] == ["Riz"]
    stranger = make_account("carol")
    res = client.get("/v1/stock", headers={**stranger.headers, "Emile-Household": str(alice.household_id)})
    assert res.status_code == 404  # never reveal other households


def test_user_without_household(app, client):
    with app.state.db.session() as session:
        __, secret = accounts.create_key(session, accounts.create_user(session, "loner", NOW), None, NOW)
    res = client.get("/v1/stock", headers={"Authorization": f"Bearer {secret}"})
    assert (res.status_code, res.json()["error"]) == (409, "conflict")


def test_admin_api_sets_up_a_user_household_and_key(client):
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert client.post("/v1/admin/users", json={"name": "bob"}).status_code == 401
    assert (
        client.post("/v1/admin/users", json={"name": "bob"}, headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    user = client.post("/v1/admin/users", json={"name": "bob"}, headers=admin).json()
    assert client.post("/v1/admin/users", json={"name": "bob"}, headers=admin).json()["error"] == "account_error"
    household = client.post(
        "/v1/admin/households", json={"name": "Home", "members": [user["id"]]}, headers=admin
    ).json()
    assert (household["timezone"], household["language"], household["currency"]) == ("Europe/Paris", "fr", "€")
    key = client.post(f"/v1/admin/users/{user['id']}/keys", json={"label": "bot"}, headers=admin).json()
    assert key["key"].startswith("emile_") and key["prefix"] == key["key"][:12]
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {key['key']}"}).json()
    assert me["households"][0]["name"] == "Home"
    partner = client.post("/v1/admin/users", json={"name": "partner"}, headers=admin).json()
    res = client.post(f"/v1/admin/households/{household['id']}/members", json={"user_id": partner["id"]}, headers=admin)
    assert [m["name"] for m in res.json()["members"]] == ["bob", "partner"]
    assert [u["name"] for u in client.get("/v1/admin/users", headers=admin).json()] == ["bob", "partner"]
    assert client.post(f"/v1/admin/keys/{key['id']}/revoke", headers=admin).status_code == 204
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {key['key']}"}).status_code == 401


def test_admin_api_is_off_without_admin_key(app, client):
    app.state.settings.admin_key = None
    res = client.get("/v1/admin/users", headers={"Authorization": f"Bearer {ADMIN_KEY}"})
    assert (res.status_code, res.json()["error"]) == (403, "forbidden")


def test_short_admin_keys_are_refused():
    from emile.service.settings import Settings

    with pytest.raises(ValueError, match="at least 24"):
        Settings(_env_file=None, admin_key="short")
    assert Settings(_env_file=None, admin_key="").admin_key is None


# --- concurrency ----------------------------------------------------------------------------------------------


def test_a_stale_write_is_rejected(app, alice):
    db = app.state.db
    with db.session() as session:
        household = accounts.find_household(session, alice.household_id)
        version = household.version
        kitchen = repository.load(session, household)
    repository.mutate(db, alice.household_id, lambda k, info: stock.put(k, "Riz", None, NOW.date()))
    stock.put(kitchen, "Pain", None, NOW.date())
    with pytest.raises(repository.Conflict), db.session() as session:
        repository.save(session, alice.household_id, kitchen, version)
    assert [e.name for e in repository.read(db, alice.household_id, lambda k, info: k.stock)] == ["Riz"]


def test_mutate_replays_on_conflict_so_no_update_is_lost(app, alice):
    """Two people tick at the same moment: the second write is replayed on the first one's result."""
    db = app.state.db
    calls = []

    def add_bread(kitchen, info):
        calls.append(1)
        if len(calls) == 1:  # someone else writes between our read and our write
            repository.mutate(
                db, alice.household_id, lambda k, i: stock.update(k, [StockAdd(name="Riz")], today=NOW.date())
            )
        return stock.put(kitchen, "Pain", None, NOW.date())

    repository.mutate(db, alice.household_id, add_bread)
    assert len(calls) == 2
    assert sorted(e.name for e in repository.read(db, alice.household_id, lambda k, info: k.stock)) == ["Pain", "Riz"]


def test_mutate_gives_up_when_the_kitchen_keeps_changing(app, alice):
    db = app.state.db

    def always_overtaken(kitchen, info):
        repository.mutate(db, alice.household_id, lambda k, i: None)
        return

    with pytest.raises(repository.Busy):
        repository.mutate(db, alice.household_id, always_overtaken)


def test_domain_errors_save_nothing(app, alice):
    db = app.state.db
    before = repository.read(db, alice.household_id, lambda k, info: k)

    def half_then_fail(kitchen, info):
        stock.put(kitchen, "Riz", None, NOW.date())
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        repository.mutate(db, alice.household_id, half_then_fail)
    assert repository.read(db, alice.household_id, lambda k, info: k) == before


def test_timestamps_come_back_aware_and_in_utc(app, alice):
    db = app.state.db
    local = datetime.datetime(2026, 11, 9, 18, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
    from emile.core import NewItem, shopping

    repository.mutate(db, alice.household_id, lambda k, i: shopping.new_list(k, [NewItem(name="Riz")], now=local))
    created = repository.read(db, alice.household_id, lambda k, info: k.current_list.created)
    assert created == local
    assert created.utcoffset() == datetime.timedelta(0)
