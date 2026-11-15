"""The CLI manages accounts against the configured database (as `docker exec emile emile …` would)."""

import pytest
from fastapi.testclient import TestClient

from emile import cli
from emile.service.app import create_app
from emile.service.settings import Settings, get_settings


@pytest.fixture
def env_db(database_url, monkeypatch):
    monkeypatch.setenv("EMILE_DATABASE_URL", database_url)
    monkeypatch.delenv("EMILE_ADMIN_KEY", raising=False)
    get_settings.cache_clear()
    yield database_url
    get_settings.cache_clear()


def run(capsys, *argv: str) -> str:
    cli.main(list(argv))
    return capsys.readouterr().out


def test_setup_creates_a_working_key(env_db, capsys):
    out = run(capsys, "setup", "bob", "--household", "Home", "--timezone", "Europe/Paris", "--boxes", "4")
    lines = out.strip().splitlines()
    assert lines[0] == "user 1: bob"
    assert lines[1] == "household 1: Home (Europe/Paris, fr)"
    key = lines[-1]
    assert key.startswith("emile_")
    with TestClient(create_app(Settings(_env_file=None, database_url=env_db))) as client:
        me = client.get("/v1/me", headers={"Authorization": f"Bearer {key}"}).json()
    assert (me["name"], me["households"][0]["meal_boxes"]) == ("bob", 4)


def test_users_keys_and_households(env_db, capsys):
    assert run(capsys, "db", "upgrade") == "database up to date\n"
    assert run(capsys, "users", "create", "alice") == "user 1: alice\n"
    run(capsys, "users", "create", "partner")
    assert run(capsys, "households", "create", "Flat", "--member", "alice", "--language", "en") == (
        "household 1: Flat (Europe/Paris, en)\n"
    )
    assert run(capsys, "households", "add-member", "1", "partner") == "partner is a member of household 1\n"
    assert "alice, partner" in run(capsys, "households", "list")
    out = run(capsys, "keys", "create", "alice", "--label", "phone")
    assert out.splitlines()[-1].startswith("emile_")
    listed = run(capsys, "keys", "list")
    assert "alice\tphone\tunused" in listed
    assert out.splitlines()[-1] not in listed  # secrets are never listed
    assert run(capsys, "keys", "revoke", "1").endswith("revoked\n")
    assert "revoked" in run(capsys, "keys", "list")
    assert run(capsys, "users", "list") == "1\talice\n2\tpartner\n"


def test_account_errors_exit_cleanly(env_db, capsys):
    run(capsys, "users", "create", "alice")
    with pytest.raises(SystemExit) as exc:
        cli.main(["users", "create", "alice"])
    assert exc.value.code == 1
    assert "already exists" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["households", "create", "X", "--timezone", "Mars/Olympus"])
    assert "unknown IANA time zone" in capsys.readouterr().err
