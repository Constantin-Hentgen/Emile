"""The calendar sync against a real CalDAV server. Run with ``-m integration`` and these variables set, e.g. a
local Radicale: EMILE_TEST_CALDAV_URL=http://127.0.0.1:5232/me/shopping/ (the calendar is created if missing),
EMILE_TEST_CALDAV_USER=me, EMILE_TEST_CALDAV_PASSWORD=secret."""

import datetime
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from emile.core import ical
from emile.service.app import create_app
from emile.service.remote import CaldavRemote, caldav_factory
from emile.service.settings import Settings
from tests.service.conftest import ADMIN_KEY

pytestmark = pytest.mark.integration

BASE = os.environ.get("EMILE_TEST_CALDAV_URL")
USER = os.environ.get("EMILE_TEST_CALDAV_USER", "")
PASSWORD = os.environ.get("EMILE_TEST_CALDAV_PASSWORD", "")
D = datetime.timedelta


@pytest.fixture
def calendar_url() -> str:
    if not BASE:
        pytest.skip("set EMILE_TEST_CALDAV_URL to run against a real CalDAV server")
    # a fresh calendar per test: MKCALENDAR it through the library
    import caldav

    url = BASE.rstrip("/") + f"-{uuid.uuid4().hex[:8]}/"
    client = caldav.DAVClient(url=BASE, username=USER, password=PASSWORD, require_tls=False, enable_rfc6764=False)
    principal_url = BASE.rsplit("/", 2)[0] + "/"
    client.principal(url=principal_url).make_calendar(name="shopping", cal_id=url.rstrip("/").rsplit("/", 1)[1])
    return url


def test_remote_round_trip_on_a_real_server(calendar_url):
    remote = CaldavRemote(calendar_url, USER, PASSWORD)
    remote.check()
    now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    start = now + D(days=2)
    uid = ical.new_uid()
    description = "Couvre : 7 jours\n\n🛒 Divers\n☐ Pain, beurre; sel\n✅ Lait"
    remote.put(uid, ical.build_event(uid, "🛒 Courses (3 articles)", description, start, start + D(hours=1), now=now))
    event = remote.get(uid)
    assert event is not None
    assert (event.summary, event.description) == ("🛒 Courses (3 articles)", description)
    assert (event.start, event.end) == (start, start + D(hours=1))
    assert [e.uid for e in remote.shopping_events()] == [uid]
    assert remote.get("missing@emile-shopping") is None
    remote.delete(uid)
    assert remote.get(uid) is None


def test_full_sync_replaces_the_list_event_on_a_real_server(calendar_url, tmp_path, make_account_for):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/emile.db",
        admin_key=ADMIN_KEY,
        secret_key="integration-secret-0123456789abcdef",
        sync_interval=0,
    )
    with TestClient(create_app(settings, remote_factory=caldav_factory)) as client:
        client.headers.update(make_account_for(client.app).headers)
        start = (datetime.datetime.now() + D(days=2)).replace(microsecond=0, second=0).isoformat()
        res = client.put("/v1/calendar", json={"url": calendar_url, "username": USER, "password": PASSWORD})
        assert res.status_code == 200, res.text
        client.post("/v1/lists", json={"items": [{"name": "Bananes", "qty": "7"}], "start_time": start})
        first = client.post("/v1/sync").json()
        assert first["status"] == "synced"
        client.post("/v1/lists/1/items/0/toggle")
        assert client.post("/v1/sync").json()["event_uid"] == first["event_uid"]
        client.post("/v1/lists", json={"items": [{"name": "Riz"}]})
        second = client.post("/v1/sync").json()
        assert (second["removed"], second["event_uid"] != first["event_uid"]) == (1, True)
        remote = CaldavRemote(calendar_url, USER, PASSWORD)
        assert [e.uid for e in remote.shopping_events()] == [second["event_uid"]]
