"""Normalisation of free text coming from clients (often an LLM): names, quantities, keeping times."""

from emile.core.limits import DEFAULT_LIMITS, Limits

MAX_NAME = 80
MAX_QTY = 40
MAX_AISLE = 60
MAX_COVERAGE = 200


def clean(value: object, max_len: int) -> str | None:
    """Collapse whitespace and cap the length; empty → ``None``. Accepts any value because clients send JSON."""
    if value is None:
        return None
    text = " ".join(str(value).split())[:max_len]
    return text or None


def name_key(name: str) -> str:
    """Matching key for names: whitespace-collapsed and case-folded, so "  bananes" matches "Bananes"."""
    return " ".join(name.split()).casefold()


def keeps_days(value: object, limits: Limits = DEFAULT_LIMITS) -> int | None:
    """How long a product keeps, in days: a positive, plausible integer, otherwise ``None`` (it keeps; no due date).

    Clients send this as a number, a numeric string or garbage; anything unusable simply means "keeps".
    """
    try:
        days = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return days if 0 < days <= limits.max_keeps_days else None


def positive_int(value: object) -> int | None:
    """A strictly positive integer, otherwise ``None`` (used for "days covered")."""
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None
