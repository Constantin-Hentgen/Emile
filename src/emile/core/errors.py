"""Domain errors. They subclass the closest built-in so callers can catch broadly (``LookupError``, ``ValueError``)."""


class EmileError(Exception):
    """Base class of every domain error raised by :mod:`emile.core`."""


class NoCurrentList(EmileError, LookupError):
    """The household has no current shopping list (create one first)."""


class StaleList(EmileError, LookupError):
    """The list id is no longer current.

    Every edit gives the list a new id, so actions addressed to an item *by position* (a chat button, a link in a
    notification) are refused instead of ticking whatever item now sits at that position.
    """


class ItemNotFound(EmileError, LookupError):
    """No item at that position in the current list, or no stock entry with that id."""


class LimitExceeded(EmileError, ValueError):
    """A configured size limit would be exceeded; nothing was changed."""
