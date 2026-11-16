"""Pluggable recipe sources.

A source looks a dish up in an open recipe collection. Emile does not write or choose recipes (no LLM inside): it
fetches public text that a client may lean on, and flags it as untrusted data.

Every source is wrapped in :class:`PoliteSource`, so caching and back-off are not left to each implementation.
"""

from emile.recipes.base import PoliteSource, Recipe, RecipeSource, build_sources, search_all
from emile.recipes.wikibooks import Wikibooks

__all__ = ["PoliteSource", "Recipe", "RecipeSource", "Wikibooks", "build_sources", "search_all"]
