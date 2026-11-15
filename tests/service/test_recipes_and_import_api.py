"""Recipe search and the Bob import over HTTP and through the CLI."""

import json
from pathlib import Path

import pytest

from emile import cli
from emile.recipes import PoliteSource, Recipe
from emile.service.settings import Settings, get_settings

FIXTURE = Path(__file__).parent.parent / "fixtures" / "bob_state.json"
STATE = json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeSource:
    name = "fake"
    languages = ("fr", "en")

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def search(self, query: str, lang: str) -> list[Recipe]:
        self.calls.append((query, lang))
        if "rien" in query:
            return []
        return [
            Recipe(title="Tortilla", url="https://example.org/tortilla", text="6 œufs", source="fake", license="CC0")
        ]


@pytest.fixture
def source(app) -> FakeSource:
    fake = FakeSource()
    app.state.recipe_sources = [PoliteSource(fake)]
    return fake


def test_recipe_search_uses_the_household_language_and_flags_text_as_untrusted(api, source):
    res = api.get("/v1/recipes/search", params={"q": " tortilla  pommes "})
    assert res.status_code == 200
    assert res.json() == {
        "query": "tortilla pommes",
        "language": "fr",
        "recipes": [{"title": "Tortilla", "url": "https://example.org/tortilla", "text": "6 œufs", "source": "fake",
                     "license": "CC0", "untrusted": True}],
    }  # fmt: skip
    api.get("/v1/recipes/search", params={"q": "tortilla pommes", "lang": "en"})
    api.get("/v1/recipes/search", params={"q": "tortilla pommes"})  # cached
    assert source.calls == [("tortilla pommes", "fr"), ("tortilla pommes", "en")]
    assert api.get("/v1/recipes/search", params={"q": "rien du tout"}).json()["recipes"] == []
    assert api.get("/v1/recipes/search").status_code == 422
    assert api.get("/v1/recipes/search", params={"q": "x"}, headers={"Authorization": "Bearer no"}).status_code == 401


def test_recipe_sources_can_be_disabled(database_url):
    from fastapi.testclient import TestClient

    from emile.service.app import create_app

    app = create_app(Settings(_env_file=None, database_url=database_url, recipe_sources=" "))
    assert app.state.recipe_sources == []
    with pytest.raises(ValueError, match="unknown recipe source"):
        create_app(Settings(_env_file=None, database_url=database_url, recipe_sources="wikibooks,nope"))
    with TestClient(app):
        pass


def test_import_bob_is_idempotent_and_keeps_ids(api):
    res = api.post("/v1/import/bob", json=STATE)
    assert res.status_code == 200
    assert res.json() == {"changed": True, "list_id": 7, "list_items": 4, "stock_entries": 6, "next_list_id": 8,
                          "next_stock_id": 16}  # fmt: skip
    again = api.post("/v1/import/bob", json=STATE).json()
    assert again == {**res.json(), "changed": False}
    lst = api.get("/v1/lists/current").json()
    assert (lst["id"], lst["event_uid"]) == (7, STATE["shopping"]["event_uid"])
    # a Telegram button sent before the switch (s:t:7:1) still ticks the right item
    assert api.post("/v1/lists/7/items/1/toggle").json()["items"][1]["done"] is True
    stock = {e["name"]: e for e in api.get("/v1/stock").json()["entries"]}
    assert stock["Riz basmati"]["id"] == 16 and stock["Saumon"]["asked"] == "2026-11-10"
    assert api.get("/v1/stock").json()["boxes"]["used"] == 2
    assert "SYNTHETIC" not in api.get("/v1/lists/current").text + api.get("/v1/stock").text


def test_import_refuses_to_overwrite_different_data_unless_asked(api):
    api.post("/v1/stock", json={"add": [{"name": "Riz"}]})
    res = api.post("/v1/import/bob", json=STATE)
    assert (res.status_code, res.json()["error"]) == (409, "would_overwrite")
    assert [e["name"] for e in api.get("/v1/stock").json()["entries"]] == ["Riz"]
    res = api.post("/v1/import/bob", json=STATE, params={"replace": "true"})
    assert res.json()["changed"] is True and res.json()["next_stock_id"] == 16
    res = api.post("/v1/import/bob", json={"memories": []})
    assert (res.status_code, res.json()["error"]) == (422, "invalid_input")


def test_client_methods(client, alice, source):
    from emile.client import Emile

    emile = Emile("http://testserver", alice.key, http=client)
    assert emile.search_recipes("tortilla", "en").recipes[0].untrusted is True
    assert emile.import_bob(STATE).list_id == 7
    assert emile.import_bob(STATE, replace=True).changed is False


def test_cli_import(database_url, monkeypatch, capsys):
    monkeypatch.setenv("EMILE_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        cli.main(["setup", "bob", "--household", "Home"])
        capsys.readouterr()
        cli.main(["import", "bob", str(FIXTURE), "--household", "1"])
        assert capsys.readouterr().out == "imported: list 7 (4 items), 6 stock entries\n"
        monkeypatch.setattr("sys.stdin", FIXTURE.open(encoding="utf-8"))
        cli.main(["import", "bob", "-", "--household", "1"])
        assert capsys.readouterr().out.startswith("already imported, nothing changed")
        for argv, message in ((["import", "bob", str(FIXTURE), "--household", "9"], "no household 9"),
                              (["import", "bob", "/nonexistent.json", "--household", "1"], "cannot read")):  # fmt: skip
            with pytest.raises(SystemExit):
                cli.main(argv)
            assert message in capsys.readouterr().err
    finally:
        get_settings.cache_clear()
