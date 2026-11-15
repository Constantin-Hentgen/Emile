"""Emile: shopping lists, kitchen stock and batch-cooked boxes, as a library and as an HTTP service.

The package is split in two layers on purpose:

- ``emile.core`` is a pure library (list editing, stock rules, due dates, aisle grouping, CalDAV rendering).
  It must stay importable without any web framework or database driver, so a client can embed the rules directly.
- ``emile.service`` is the FastAPI application, persistence and background jobs built on top of the core.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version release-please writes into pyproject.toml.
    __version__ = version("emile")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.3.0"
