"""Recipe sources: the Wikibooks port (against a fake MediaWiki API, never the network) and the politeness rules."""

import httpx2
import pytest

from emile.recipes import PoliteSource, Recipe, Wikibooks, build_sources, search_all
from emile.recipes.base import BACKOFF_SECONDS, CACHE_SECONDS, RateLimited
from emile.recipes.wikibooks import USER_AGENT


def fake_wikibooks(calls: list, *, status: int = 200) -> Wikibooks:
    def handler(request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        calls.append((request.url.host, params, request.headers["user-agent"]))
        if status != 200:
            return httpx2.Response(status)
        if params.get("list") == "search":
            titles = [
                "Livre de cuisine/Recettes végétariennes",
                "Apprendre à cuisiner/Les bases",
                "Livre de cuisine/Tortilla",
            ]
            return httpx2.Response(200, json={"query": {"search": [{"title": t} for t in titles]}})
        page = {
            "title": "Livre de cuisine/Tortilla",
            "extract": "== Ingrédients ==\n6 œufs\n\n\n\n== Préparation ==\nCuire.",
        }
        return httpx2.Response(200, json={"query": {"pages": {"1": page}}})

    http = httpx2.Client(transport=httpx2.MockTransport(handler), headers={"User-Agent": USER_AGENT})
    return Wikibooks(http=http)


def test_wikibooks_returns_recipes_without_summary_pages():
    """Port of Bob's test_search_recipe_returns_open_recipes_without_summary_pages."""
    calls = []
    (recipe,) = fake_wikibooks(calls).search("tortilla pommes de terre", "fr")
    assert (recipe.title, recipe.url) == ("Tortilla", "https://fr.wikibooks.org/wiki/Livre_de_cuisine/Tortilla")
    assert "6 œufs" in recipe.text and "\n\n\n" not in recipe.text
    assert (recipe.source, recipe.license) == ("wikibooks", "CC BY-SA 4.0")
    assert calls[0][1]["srsearch"] == "intitle:tortilla intitle:pommes intitle:terre"  # title match first
    assert calls[-1][1]["titles"] == "Livre de cuisine/Tortilla"
    assert {host for host, __, __ in calls} == {"fr.wikibooks.org"}


def test_user_agent_is_generic():
    calls = []
    fake_wikibooks(calls).search("tortilla", "fr")
    agent = calls[0][2]
    assert agent.startswith("emile/") and "@" not in agent and "http" not in agent


def test_english_cookbook_uses_its_own_site_and_namespace():
    calls = []
    assert fake_wikibooks(calls).search("pancakes", "en") == []  # the fake only has French titles
    assert calls[0][0] == "en.wikibooks.org" and calls[0][1]["srnamespace"] == "102"


def test_http_429_raises_rate_limited():
    with pytest.raises(RateLimited):
        fake_wikibooks([], status=429).search("tortilla", "fr")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Scripted:
    name = "scripted"
    languages = ("fr", "en")

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def search(self, query: str, lang: str) -> list[Recipe]:
        self.calls.append(query)
        if self.fail:
            raise self.fail
        return [Recipe(title=query.title(), url="u", text="t", source=self.name)]


def test_results_are_cached_for_a_day_per_query_and_language():
    """Port of Bob's test_search_recipe_caches_and_backs_off_on_429 (cache part)."""
    clock, inner = Clock(), Scripted()
    source = PoliteSource(inner, monotonic=clock)
    assert source.search("Tortilla", "fr") == source.search("  tortilla ", "fr")
    assert inner.calls == ["Tortilla"]
    source.search("tortilla", "en")
    assert len(inner.calls) == 2
    clock.t += CACHE_SECONDS + 1
    source.search("tortilla", "fr")
    assert len(inner.calls) == 3


def test_backs_off_after_429_then_resumes():
    clock, inner = Clock(), Scripted()
    source = PoliteSource(inner, monotonic=clock)
    inner.fail = RateLimited()
    assert source.search("omelette", "fr") == [] and source.search("crêpes", "fr") == []
    assert inner.calls == ["omelette"]  # backed off: no second call
    inner.fail = None
    clock.t += BACKOFF_SECONDS + 1
    assert [r.title for r in source.search("crêpes", "fr")] == ["Crêpes"]


def test_failures_and_unusable_queries_give_no_result():
    inner = Scripted()
    source = PoliteSource(inner)
    inner.fail = RuntimeError("site down")
    assert source.search("tortilla", "fr") == []
    inner.fail = None
    assert [r.title for r in source.search("tortilla", "fr")] == ["Tortilla"]  # errors are not cached
    assert source.search("   ", "fr") == [] and source.search("tortilla", "de") == []
    assert inner.calls == ["tortilla", "tortilla"]


def test_search_all_and_configuration():
    a, b = PoliteSource(Scripted()), PoliteSource(Scripted())
    assert len(search_all([a, b], "tortilla", "fr", limit=2)) == 2
    assert len(search_all([a, b], "tortilla", "fr", limit=1)) == 1
    assert search_all([], "tortilla", "fr") == []
    assert [s.name for s in build_sources(["wikibooks"], timeout=3)] == ["wikibooks"]
    with pytest.raises(ValueError, match="unknown recipe source"):
        build_sources(["marmiton"], timeout=3)
