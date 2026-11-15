"""The recipe source contract, and the politeness wrapper applied to every source."""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)

CACHE_SECONDS = 24 * 3600
"""One lookup per dish and per day is plenty: recipes don't change."""
BACKOFF_SECONDS = 600
"""After a refusal (HTTP 429), leave the site alone for ten minutes."""
MAX_CACHE_ENTRIES = 500
MAX_QUERY = 80


class Recipe(BaseModel):
    """A recipe found in an open collection: plain text (ingredients and method), truncated."""

    title: str
    url: str
    text: str
    source: str
    license: str | None = None


class RateLimited(Exception):
    """The site asked us to slow down (HTTP 429)."""


class RecipeSource(Protocol):
    """A recipe collection. ``search`` may raise :class:`RateLimited` or any error: the wrapper handles both."""

    name: str
    languages: tuple[str, ...]

    def search(self, query: str, lang: str) -> list[Recipe]: ...


class PoliteSource:
    """Wrap a source with a per-query cache, a back-off after HTTP 429, and "no result" instead of errors.

    A recipe lookup is a nice-to-have: when the site is down or refuses, the client simply gets nothing and writes
    the recipe itself. Thread-safe, since API requests run in a thread pool.
    """

    def __init__(self, source: RecipeSource, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.source = source
        self.name = source.name
        self.languages = source.languages
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, str], tuple[float, list[Recipe]]] = OrderedDict()
        self._blocked_until = 0.0

    def search(self, query: str, lang: str) -> list[Recipe]:
        query = " ".join((query or "").split())[:MAX_QUERY]
        if not query or lang not in self.languages:
            return []
        key = (lang, query.casefold())
        now = self._monotonic()
        with self._lock:
            if now < self._blocked_until:
                return []
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < CACHE_SECONDS:
                return cached[1]
        try:
            found = self.source.search(query, lang)
        except RateLimited:
            with self._lock:
                self._blocked_until = self._monotonic() + BACKOFF_SECONDS
            logger.warning("%s asked us to slow down: no lookups for %s s", self.name, BACKOFF_SECONDS)
            return []
        except Exception as e:
            logger.warning("%s unavailable: %s: %s", self.name, type(e).__name__, e)
            return []
        with self._lock:
            self._cache[key] = (self._monotonic(), found)
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)
        return found


def build_sources(names: Sequence[str], *, timeout: float) -> list[PoliteSource]:
    """The configured sources, in order. Unknown names are a configuration error."""
    from emile.recipes.wikibooks import Wikibooks

    known: dict[str, Callable[[], RecipeSource]] = {"wikibooks": lambda: Wikibooks(timeout=timeout)}
    sources = []
    for name in names:
        if name not in known:
            raise ValueError(f"unknown recipe source {name!r} (available: {', '.join(sorted(known))})")
        sources.append(PoliteSource(known[name]()))
    return sources


def search_all(sources: Sequence[PoliteSource], query: str, lang: str, limit: int = 3) -> list[Recipe]:
    """Ask each source in turn until ``limit`` recipes are found."""
    found: list[Recipe] = []
    for source in sources:
        found += source.search(query, lang)
        if len(found) >= limit:
            break
    return found[:limit]
