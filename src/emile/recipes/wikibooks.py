"""Wikibooks cookbooks (CC BY-SA) through the MediaWiki API, no key needed: "Livre de cuisine" on fr.wikibooks,
"Cookbook" on en.wikibooks. Only the dish name is sent."""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx2

from emile import __version__
from emile.recipes.base import RateLimited, Recipe

# Generic on purpose: identifies the software, never the person running it.
USER_AGENT = f"emile/{__version__} (self-hosted groceries service)"
MAX_RESULTS = 2
MAX_CHARS = 1500
STOPWORDS = frozenset(
    [
        "au",
        "aux",
        "à",
        "la",
        "le",
        "les",
        "de",
        "des",
        "du",
        "et",
        "en",
        "avec",
        "sans",
        "d",
        "l",
        "un",
        "une",
        "and",
        "with",
        "the",
        "of",
        "in",
        "a",
        "an",
        "without",
    ]
)


@dataclass(frozen=True, slots=True)
class Site:
    url: str
    prefix: str
    """Title prefix of recipe pages."""
    namespace: int
    not_a_recipe: re.Pattern[str]
    """Index and category pages to skip."""


SITES = {
    "fr": Site(
        "https://fr.wikibooks.org",
        "Livre de cuisine/",
        0,
        re.compile(r"/(?:Liste|Recettes?\b|Catégorie|Sommaire|Index|Intersection)", re.IGNORECASE),
    ),
    "en": Site(
        "https://en.wikibooks.org",
        "Cookbook:",
        102,
        re.compile(r":(?:Table of Contents|Recipes\b|Cuisine\b|Ingredients\b|Equipment\b|Techniques\b)", re.IGNORECASE),
    ),
}


class Wikibooks:
    """Recipe source backed by the Wikibooks cookbooks."""

    name = "wikibooks"
    languages = tuple(SITES)
    license = "CC BY-SA 4.0"

    def __init__(self, *, timeout: float = 8.0, http: httpx2.Client | None = None) -> None:
        self._http = http or httpx2.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})

    def _get(self, site: Site, **params: str | int) -> dict[str, object]:
        response = self._http.get(f"{site.url}/w/api.php", params={**params, "format": "json"})
        if response.status_code == 429:
            raise RateLimited
        response.raise_for_status()
        data: dict[str, object] = response.json()
        return data

    @staticmethod
    def page_url(site: Site, title: str) -> str:
        return f"{site.url}/wiki/" + quote(title.replace(" ", "_"), safe=":/")

    def search(self, query: str, lang: str) -> list[Recipe]:
        site = SITES[lang]
        # The dish name in the title first (all words, then the main word), full-text search as a last resort.
        words = [w for w in re.findall(r"[\wÀ-ÿ'-]+", query.lower()) if w not in STOPWORDS and len(w) > 2]
        attempts = (
            ([" ".join(f"intitle:{w}" for w in words)] if len(words) > 1 else [])
            + ([f"intitle:{words[0]}"] if words else [])
            + [query]
        )
        titles: list[str] = []
        for attempt in attempts:
            found = self._get(
                site, action="query", list="search", srsearch=attempt, srnamespace=site.namespace, srlimit=10
            )
            for result in _dig(found, "query", "search") or []:
                title = result["title"]
                if title.startswith(site.prefix) and not site.not_a_recipe.search(title) and title not in titles:
                    titles.append(title)
            if len(titles) >= MAX_RESULTS:
                break
        titles = titles[:MAX_RESULTS]
        if not titles:
            return []
        pages = self._get(site, action="query", prop="extracts", explaintext=1, titles="|".join(titles))
        extracts = {
            p.get("title"): (p.get("extract") or "").strip() for p in (_dig(pages, "query", "pages") or {}).values()
        }
        recipes = []
        for title in titles:
            text = re.sub(r"\n{3,}", "\n\n", extracts.get(title) or "")
            if text:
                recipes.append(
                    Recipe(
                        title=title.removeprefix(site.prefix),
                        url=self.page_url(site, title),
                        text=text[:MAX_CHARS] + ("…" if len(text) > MAX_CHARS else ""),
                        source=self.name,
                        license=self.license,
                    )
                )
        return recipes


def _dig(data: object, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data
