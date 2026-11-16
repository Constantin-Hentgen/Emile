"""CalDAV event sync: configuration, what is written, what is replaced, what is never touched."""

import asyncio
import datetime
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from emile.core import ical, tasks
from emile.service import sync
from emile.service.app import create_app, sync_loop
from emile.service.settings import Settings
from emile.service.tables import CalendarConfig
from tests.service.conftest import ADMIN_KEY
from tests.service.fake_calendar import FakeServer

D = datetime.timedelta
URL = "https://dav.example.org/me/shopping/"
SECRET_KEY = "test-secret-key-0123456789abcdef"
PASSWORD = "correct horse battery staple"
# Saturday 14 November 2026, 10:30 in Paris
SAT = "2026-11-14T10:30:00"
SAT_UTC = datetime.datetime(2026, 11, 14, 9, 30, tzinfo=datetime.UTC)
BOB_STATE = json.loads((Path(__file__).parent.parent / "fixtures" / "bob_state.json").read_text(encoding="utf-8"))


@pytest.fixture
def server() -> FakeServer:
    fake = FakeServer()
    fake.add_calendar(URL, PASSWORD)
    return fake


@pytest.fixture
def settings(database_url) -> Settings:
    return Settings(
        _env_file=None, database_url=database_url, admin_key=ADMIN_KEY, secret_key=SECRET_KEY, sync_interval=0
    )


@pytest.fixture
def app(settings, clock, server):
    return create_app(settings, clock=clock, remote_factory=server.connect)


def connect(api, **overrides):
    body = {"url": URL, "username": "me", "password": PASSWORD} | overrides
    return api.put("/v1/calendar", json=body)


def new_list(api, **extra):
    body = {
        "items": [{"name": "Bananes", "qty": "7", "aisle": "🥬 Fruits"}, {"name": "Lait", "aisle": "🥛 Crèmerie"}],
        "days": 7,
        "start_time": SAT,
    } | extra
    res = api.post("/v1/lists", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def shopping_uids(server) -> set[str]:
    return {u for u in server.uids(URL) if ical.is_shopping_uid(u)}


# --- configuration ----------------------------------------------------------------------------------------------


def test_calendar_is_stored_encrypted_and_never_returned(api, app):
    res = connect(api)
    assert res.status_code == 200
    assert res.json() == {"url": URL, "username": "me", "mode": "event", "last_sync_at": None, "last_error": None}
    assert PASSWORD not in api.get("/v1/calendar").text
    with app.state.db.session() as session:
        stored = session.scalars(select(CalendarConfig)).one().password_encrypted
    assert PASSWORD not in stored
    assert app.state.vault.decrypt(stored) == PASSWORD


def test_a_wrong_password_or_url_is_refused_before_saving(api, server):
    res = connect(api, password="wrong")
    assert (res.status_code, res.json()["error"]) == (502, "calendar_error")
    assert "wrong password" in res.json()["detail"]
    assert PASSWORD not in res.text
    assert api.get("/v1/calendar").status_code == 404
    assert connect(api, url="https://dav.example.org/nowhere/").status_code == 502


def test_only_http_urls_are_accepted(api):
    res = connect(api, url="file:///etc/passwd")
    assert (res.status_code, res.json()["error"]) == (422, "validation_error")


def test_calendar_needs_a_secret_key(settings, clock, server, make_account):
    settings.secret_key = None
    with TestClient(create_app(settings, clock=clock, remote_factory=server.connect)) as client:
        account = make_account("bob")
        res = client.put(
            "/v1/calendar",
            json={"url": URL, "username": "me", "password": PASSWORD},
            headers=account.headers,
        )
    assert (res.status_code, res.json()["error"]) == (503, "calendar_unavailable")
    assert "EMILE_SECRET_KEY" in res.json()["detail"]


def test_calendar_can_be_replaced_and_removed(api, server):
    connect(api)
    other = "https://dav.example.org/me/other/"
    server.add_calendar(other, "pw2")
    assert connect(api, url=other, password="pw2").json()["url"] == other
    assert api.delete("/v1/calendar").status_code == 204
    assert api.get("/v1/calendar").status_code == 404
    assert api.delete("/v1/calendar").status_code == 404
    assert api.post("/v1/sync").status_code == 404


def test_calendars_are_per_household(api, make_account, server):
    connect(api)
    stranger = make_account("carol")
    assert TestClient(api.app).get("/v1/calendar", headers=stranger.headers).status_code == 404


# --- what is written --------------------------------------------------------------------------------------------


def test_sync_writes_one_event_with_the_checklist(api, server):
    connect(api)
    lst = new_list(api)
    assert lst["event_start"] == "2026-11-14T09:30:00Z"
    res = api.post("/v1/sync")
    assert res.status_code == 200
    body = res.json()
    assert (body["status"], body["removed"]) == ("synced", 0)
    event = server.event(URL, body["event_uid"])
    assert body["event_uid"].endswith(ical.UID_SUFFIX)
    assert (event.start, event.end) == (SAT_UTC, SAT_UTC + D(hours=1))
    assert event.summary == "🛒 Courses (2 articles, 7 jours)"
    assert event.description == "Couvre : 7 jours\n\n🥬 Fruits\n☐ Bananes — 7\n\n🥛 Crèmerie\n☐ Lait"
    assert api.get("/v1/lists/current").json()["event_uid"] == body["event_uid"]
    assert api.get("/v1/calendar").json()["last_error"] is None
    assert api.get("/v1/calendar").json()["last_sync_at"] == "2026-11-09T17:00:00Z"


def test_the_event_follows_the_household_language(make_account, client, server):
    english = make_account("dave", language="en")
    client.headers.update(english.headers)
    connect(client)
    new_list(client, days=3)
    uid = client.post("/v1/sync").json()["event_uid"]
    assert server.event(URL, uid).summary == "🛒 Shopping (2 items, 3 days)"
    assert server.event(URL, uid).description.startswith("Covers: 3 days\n\n")


def test_a_naive_start_time_is_in_the_households_time_zone(api):
    assert new_list(api, start_time="2026-11-14T10:30:00")["event_start"] == "2026-11-14T09:30:00Z"  # Paris, UTC+1
    assert new_list(api, start_time="2026-11-14T10:30:00+02:00")["event_start"] == "2026-11-14T08:30:00Z"
    assert new_list(api, start_time=SAT, duration_minutes=90)["event_end"] == "2026-11-14T11:00:00Z"


def test_a_list_without_a_slot_has_nothing_to_sync(api, server):
    connect(api)
    assert api.post("/v1/sync").json()["status"] == "skipped"  # no list yet
    api.post("/v1/lists", json={"items": [{"name": "Riz"}]})
    res = api.post("/v1/sync").json()
    assert (res["status"], res["event_uid"]) == ("skipped", None)
    assert "no shopping slot" in res["detail"]
    assert server.calendars[URL] == {}


def test_a_slot_in_the_past_is_refused(api):
    res = api.post("/v1/lists", json={"items": [{"name": "Riz"}], "start_time": "2026-11-01T10:00:00"})
    assert (res.status_code, res.json()["error"]) == (422, "invalid_input")


# --- keeping the event up to date ------------------------------------------------------------------------------


def test_ticking_items_updates_the_checklist_in_place(api, server):
    connect(api)
    lst = new_list(api)
    uid = api.post("/v1/sync").json()["event_uid"]
    api.post(f"/v1/lists/{lst['id']}/items/0/toggle")
    res = api.post("/v1/sync").json()
    assert (res["status"], res["event_uid"], res["removed"]) == ("synced", uid, 0)
    assert "✅ Bananes — 7" in server.event(URL, uid).description
    assert shopping_uids(server) == {uid}


def test_editing_by_name_keeps_the_same_event(api, server):
    connect(api)
    lst = new_list(api)
    uid = api.post("/v1/sync").json()["event_uid"]
    edited = api.patch("/v1/lists/current", json={"check": ["lait"], "add": [{"name": "Pain"}]}).json()["list"]
    assert edited["id"] != lst["id"]
    assert api.post("/v1/sync").json()["event_uid"] == uid
    assert shopping_uids(server) == {uid}
    assert "☐ Pain" in server.event(URL, uid).description
    assert server.event(URL, uid).summary.startswith("🛒 Courses (3 articles")


def test_moving_the_trip_moves_the_event(api, server):
    connect(api)
    lst = new_list(api)
    uid = api.post("/v1/sync").json()["event_uid"]
    res = api.put(f"/v1/lists/{lst['id']}/slot", json={"start_time": "2026-11-15T16:00:00", "duration_minutes": 45})
    assert res.status_code == 200
    assert api.post("/v1/sync").json()["event_uid"] == uid
    event = server.event(URL, uid)
    assert (event.start, event.end) == (
        datetime.datetime(2026, 11, 15, 15, 0, tzinfo=datetime.UTC),
        datetime.datetime(2026, 11, 15, 15, 45, tzinfo=datetime.UTC),
    )
    stale = api.put(f"/v1/lists/{lst['id'] + 5}/slot", json={"start_time": SAT})
    assert (stale.status_code, stale.json()["error"]) == (409, "stale_list")


def test_nothing_is_sent_when_nothing_changed(api, server, app, clock):
    connect(api)
    new_list(api)
    api.post("/v1/sync")
    server.calls.clear()
    with app.state.db.session() as session:
        assert session.scalars(select(CalendarConfig)).one().synced_digest
    outcome = sync.sync_household(app.state.db, app.state.vault, server.connect, 1, clock())
    assert outcome.status == "unchanged"
    assert server.calls == []


def test_a_forced_sync_repairs_an_event_deleted_in_the_calendar(api, server):
    connect(api)
    new_list(api)
    uid = api.post("/v1/sync").json()["event_uid"]
    del server.calendars[URL][uid]
    assert api.post("/v1/sync").json()["event_uid"] == uid
    assert uid in server.uids(URL)


# --- replacing a list: only Emile's own events go ---------------------------------------------------------------


def test_a_new_list_replaces_the_upcoming_event_and_nothing_else(api, server):
    connect(api)
    new_list(api)
    old_uid = api.post("/v1/sync").json()["event_uid"]
    server.add_foreign_event(URL, "dentist@example.org", SAT_UTC + D(days=1))
    past_uid = f"last-week{ical.UID_SUFFIX}"  # Emile's own past event: history, it stays
    server.calendars[URL][past_uid] = ical.build_event(
        past_uid, "old", "old", SAT_UTC - D(days=7), SAT_UTC - D(days=7) + D(hours=1), now=SAT_UTC - D(days=9)
    )

    lst = new_list(api, items=[{"name": "Riz"}])  # no start_time: reuses the slot
    assert lst["event_start"] == "2026-11-14T09:30:00Z"
    assert lst["event_uid"] is None
    res = api.post("/v1/sync").json()
    assert (res["status"], res["removed"]) == ("synced", 1)
    assert res["event_uid"] != old_uid
    assert old_uid not in server.uids(URL)
    assert server.uids(URL) == {res["event_uid"], "dentist@example.org", past_uid}


def test_the_new_event_is_verified_before_the_old_one_is_removed(api, server):
    connect(api)
    new_list(api)
    old_uid = api.post("/v1/sync").json()["event_uid"]
    new_list(api, items=[{"name": "Riz"}])
    server.drop_writes = True  # the server says OK but keeps nothing
    res = api.post("/v1/sync")
    assert (res.status_code, res.json()["error"]) == (502, "calendar_error")
    assert "read-back" in res.json()["detail"]
    assert old_uid in server.uids(URL)  # still there: nothing was lost
    assert "read-back" in api.get("/v1/calendar").json()["last_error"]
    server.drop_writes = False
    assert api.post("/v1/sync").status_code == 200
    assert api.get("/v1/calendar").json()["last_error"] is None
    assert old_uid not in server.uids(URL)


def test_a_server_that_is_down_is_reported_and_retried(api, server):
    connect(api)
    new_list(api)
    server.fail = True
    res = api.post("/v1/sync")
    assert (res.status_code, res.json()["error"]) == (502, "calendar_error")
    assert "down" in api.get("/v1/calendar").json()["last_error"]
    assert api.get("/v1/lists/current").status_code == 200  # the shopping list itself is unaffected
    server.fail = False
    assert api.post("/v1/sync").json()["status"] == "synced"


# --- taking over Bob's calendar -------------------------------------------------------------------------------


def test_bobs_existing_event_is_taken_over_not_duplicated(api, server, app):
    legacy_uid = "7f1e0c1e-0000-4000-8000-000000000001@running-coach-shopping"
    server.calendars[URL][legacy_uid] = ical.build_event(
        legacy_uid, "🛒 Courses (old)", "old", SAT_UTC, SAT_UTC + D(hours=1), now=SAT_UTC - D(days=3)
    )
    server.add_foreign_event(URL, "run-5k@running-coach", SAT_UTC + D(days=1), "Run")  # Bob's workout: not ours
    state = json.loads(json.dumps(BOB_STATE))
    state["shopping"]["event_uid"] = legacy_uid
    state["shopping"]["event_start"] = SAT_UTC.isoformat()
    state["shopping"]["event_end"] = (SAT_UTC + D(hours=1)).isoformat()
    assert api.post("/v1/import/bob", json=state).status_code == 200
    connect(api)
    imported = api.get("/v1/lists/current").json()
    assert imported["event_uid"] == legacy_uid

    res = api.post("/v1/sync").json()
    assert (res["event_uid"], res["removed"]) == (legacy_uid, 0)
    assert shopping_uids(server) == {legacy_uid}  # same event, updated in place
    assert "Courses" in server.event(URL, legacy_uid).summary
    assert server.event(URL, legacy_uid).description != "old"
    assert "run-5k@running-coach" in server.uids(URL)

    # a new list gets Emile's own suffix and replaces the legacy event
    new_list(api, items=[{"name": "Riz"}])
    fresh = api.post("/v1/sync").json()
    assert fresh["event_uid"].endswith(ical.UID_SUFFIX)
    assert (fresh["removed"], legacy_uid in server.uids(URL)) == (1, False)
    assert "run-5k@running-coach" in server.uids(URL)


def test_an_untracked_legacy_event_is_replaced_by_the_first_sync(api, server):
    """Bob's event exists in the calendar but the list was not imported: it is still Emile's to replace."""
    legacy_uid = "abc@running-coach-shopping"
    server.calendars[URL][legacy_uid] = ical.build_event(
        legacy_uid, "🛒 Courses", "old", SAT_UTC, SAT_UTC + D(hours=1), now=SAT_UTC - D(days=3)
    )
    connect(api)
    new_list(api)
    res = api.post("/v1/sync").json()
    assert (res["removed"], shopping_uids(server)) == (1, {res["event_uid"]})


# --- background pass ------------------------------------------------------------------------------------------


def test_the_background_pass_syncs_every_household_and_survives_a_failing_one(api, server, make_account, app, clock):
    connect(api)
    new_list(api)
    other_url = "https://dav.example.org/carol/shopping/"
    server.add_calendar(other_url, "pw")
    carol = make_account("carol")
    client = TestClient(app)
    client.put("/v1/calendar", json={"url": other_url, "username": "me", "password": "pw"}, headers=carol.headers)
    client.post("/v1/lists", json={"items": [{"name": "Riz"}], "start_time": SAT}, headers=carol.headers)
    server.calendars[other_url].clear()
    server.passwords[other_url] = "changed"  # carol's calendar now refuses us

    results = sync.sync_all(app.state.db, app.state.vault, server.connect, clock())
    assert {k: (v if isinstance(v, str) else v.status) for k, v in results.items()} == {
        1: "synced",
        2: results[2],
    }
    assert isinstance(results[2], str)
    assert len(shopping_uids(server)) == 1
    assert "wrong password" in client.get("/v1/calendar", headers=carol.headers).json()["last_error"]


def test_the_background_loop_syncs_on_its_own(api, server, app):
    connect(api)
    new_list(api)

    async def run_for_a_moment() -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(sync_loop(app, 0.01), timeout=0.5)

    asyncio.run(run_for_a_moment())
    assert len(shopping_uids(server)) == 1
    assert api.get("/v1/calendar").json()["last_sync_at"] == "2026-11-09T17:00:00Z"


def test_secrets_must_be_long_enough():
    with pytest.raises(ValueError, match="EMILE_SECRET_KEY must be at least 24 characters"):
        Settings(_env_file=None, secret_key="short")
    assert Settings(_env_file=None, secret_key="").secret_key is None


def test_the_python_client_covers_the_calendar(client, alice, server):
    from emile.client import ApiError, Emile

    emile = Emile("http://testserver", alice.key, http=client)
    assert emile.calendar() is None
    assert emile.set_calendar(URL, "me", PASSWORD).url == URL
    assert emile.calendar().last_sync_at is None
    lst = emile.create_list([{"name": "Riz"}], start_time=datetime.datetime(2026, 11, 14, 10, 30), duration_minutes=30)
    assert lst.event_end - lst.event_start == D(minutes=30)
    moved = emile.set_slot(lst.id, datetime.datetime(2026, 11, 15, 10, 0))
    assert moved.event_start == datetime.datetime(2026, 11, 15, 9, 0, tzinfo=datetime.UTC)
    result = emile.sync()
    assert (result.status, len(shopping_uids(server))) == ("synced", 1)
    with pytest.raises(ApiError) as exc:
        emile.set_calendar(URL, "me", "wrong")
    assert exc.value.error == "calendar_error"
    emile.remove_calendar()
    assert emile.calendar() is None


# --- tasks mode: one task per item, ticked in both directions ---------------------------------------------------


def tasks_mode(api):
    res = connect(api, mode="tasks")
    assert (res.status_code, res.json()["mode"]) == (200, "tasks")


def stock_names(api):
    return [e["name"] for e in api.get("/v1/stock").json()["entries"]]


def task_named(server, name):
    return next(t for t in server.tasks(URL).values() if t.summary.startswith(name))


def test_one_task_per_item_with_the_aisle_as_category(api, server):
    tasks_mode(api)
    new_list(api)
    res = api.post("/v1/sync").json()
    assert (res["status"], res["pushed"], res["pulled"]) == ("synced", 2, 0)
    found = server.tasks(URL)
    assert sorted((t.summary, t.category, t.done) for t in found.values()) == [
        ("Bananes — 7", "🥬 Fruits", False),
        ("Lait", "🥛 Crèmerie", False),
    ]
    assert all(uid.endswith(ical.UID_SUFFIX) for uid in found)
    assert [u for u in server.uids(URL) if u not in found] == []  # no event in tasks mode
    again = api.post("/v1/sync").json()
    assert (again["status"], again["pushed"], again["pulled"]) == ("unchanged", 0, 0)


def test_a_task_completed_on_the_phone_ticks_the_item_and_fills_the_stock(api, server):
    tasks_mode(api)
    new_list(api)
    api.post("/v1/sync")
    server.complete(URL, task_named(server, "Lait").uid)
    res = api.post("/v1/sync").json()
    assert (res["pulled"], res["pushed"]) == (1, 0)
    items = {i["name"]: i["done"] for i in api.get("/v1/lists/current").json()["items"]}
    assert items == {"Bananes": False, "Lait": True}
    assert stock_names(api) == ["Lait"]
    assert api.post("/v1/sync").json()["status"] == "unchanged"


def test_unticking_on_the_phone_takes_the_item_out_of_the_stock(api, server):
    tasks_mode(api)
    lst = new_list(api)
    api.post(f"/v1/lists/{lst['id']}/items/1/toggle")
    api.post("/v1/sync")
    assert task_named(server, "Lait").done
    server.complete(URL, task_named(server, "Lait").uid, done=False)
    assert api.post("/v1/sync").json()["pulled"] == 1
    assert stock_names(api) == []


def test_an_item_ticked_through_the_api_completes_its_task(api, server):
    tasks_mode(api)
    lst = new_list(api)
    api.post("/v1/sync")
    api.post(f"/v1/lists/{lst['id']}/items/0/toggle")
    res = api.post("/v1/sync").json()
    assert (res["pushed"], res["pulled"]) == (1, 0)
    assert task_named(server, "Bananes").done and not task_named(server, "Lait").done


def test_both_sides_changing_different_items_merge(api, server):
    tasks_mode(api)
    lst = new_list(api)
    api.post("/v1/sync")
    api.post(f"/v1/lists/{lst['id']}/items/0/toggle")  # Bananes bought in the shop, through the API
    server.complete(URL, task_named(server, "Lait").uid)  # Lait ticked on the phone
    res = api.post("/v1/sync").json()
    assert (res["pushed"], res["pulled"]) == (1, 1)
    assert [i["done"] for i in api.get("/v1/lists/current").json()["items"]] == [True, True]
    assert all(t.done for t in server.tasks(URL).values())


def test_when_the_record_is_lost_the_list_wins(api, server, app):
    tasks_mode(api)
    new_list(api)
    api.post("/v1/sync")
    server.complete(URL, task_named(server, "Lait").uid)
    with app.state.db.session() as session:
        session.scalars(select(CalendarConfig)).one().synced_state = None  # e.g. restored from an old backup
    res = api.post("/v1/sync").json()
    assert (res["pulled"], res["pushed"]) == (0, 1)
    assert not task_named(server, "Lait").done
    assert stock_names(api) == []


def test_editing_by_name_keeps_the_other_tasks_and_replaces_the_removed_ones(api, server):
    tasks_mode(api)
    new_list(api)
    api.post("/v1/sync")
    banana = task_named(server, "Bananes").uid
    api.patch("/v1/lists/current", json={"remove": ["lait"], "add": [{"name": "Pain", "aisle": "🥖"}]})
    res = api.post("/v1/sync").json()
    assert (res["pushed"], res["removed"]) == (1, 1)
    assert sorted(t.summary for t in server.tasks(URL).values()) == ["Bananes — 7", "Pain"]
    assert task_named(server, "Bananes").uid == banana


def test_a_new_list_replaces_all_the_tasks(api, server):
    tasks_mode(api)
    new_list(api)
    api.post("/v1/sync")
    old = set(server.tasks(URL))
    server.add_foreign_event(URL, "dentist@example.org", SAT_UTC)
    api.post("/v1/lists", json={"items": [{"name": "Riz"}]})
    res = api.post("/v1/sync").json()
    assert (res["pushed"], res["removed"]) == (1, 2)
    assert not old & set(server.tasks(URL))
    assert "dentist@example.org" in server.uids(URL)  # the household's own entries are never touched


def test_a_task_deleted_in_the_calendar_is_written_again(api, server):
    tasks_mode(api)
    new_list(api)
    api.post("/v1/sync")
    del server.calendars[URL][task_named(server, "Lait").uid]
    assert api.post("/v1/sync").json()["pushed"] == 1
    assert len(server.tasks(URL)) == 2


def test_switching_between_event_and_tasks_cleans_up_after_itself(api, server):
    connect(api)
    new_list(api)
    api.post("/v1/sync")
    assert len(shopping_uids(server)) == 1
    tasks_mode(api)
    api.post("/v1/sync")
    assert len(server.tasks(URL)) == 2 and len(server.calendars[URL]) == 2  # the event is gone
    connect(api)  # back to one event
    res = api.post("/v1/sync").json()
    assert res["status"] == "synced" and server.tasks(URL) == {}
    assert len(server.calendars[URL]) == 1


def test_bobs_old_tasks_are_taken_over_too(api, server):
    tasks_mode(api)
    new_list(api)
    server.calendars[URL]["old@running-coach-shopping"] = tasks.build_todo(
        "old@running-coach-shopping", "Riz", None, False, now=SAT_UTC
    )
    server.add_foreign_event(URL, "run@running-coach", SAT_UTC)
    api.post("/v1/sync")
    assert "old@running-coach-shopping" not in server.uids(URL)
    assert "run@running-coach" in server.uids(URL)


def test_a_full_stock_blocks_pulling_a_tick_and_says_so(api, server, app):
    tasks_mode(api)
    new_list(api)
    api.post("/v1/sync")
    app.state.settings.max_stock_items = 1
    api.post("/v1/stock", json={"add": [{"name": "Riz"}]})
    server.complete(URL, task_named(server, "Lait").uid)
    res = api.post("/v1/sync")
    assert (res.status_code, res.json()["error"]) == (502, "calendar_error")
    assert "stock full" in api.get("/v1/calendar").json()["last_error"]


def test_a_dropped_task_write_is_caught_by_the_read_back(api, server):
    tasks_mode(api)
    new_list(api)
    server.drop_writes = True
    res = api.post("/v1/sync")
    assert (res.status_code, res.json()["error"]) == (502, "calendar_error")
    assert "read-back" in res.json()["detail"]
