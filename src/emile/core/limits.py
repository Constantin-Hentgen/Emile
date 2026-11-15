"""Size and time limits, configurable per deployment.

The defaults come from the Bob bot: a list of 60 items still fits in one Telegram message with one button per item
and per aisle (Telegram allows 100 buttons), and 120 stock entries keeps a prompt short.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Limits:
    """Limits applied by the core rules."""

    max_list_items: int = 60
    """Items in one shopping list."""
    max_stock_items: int = 120
    """Entries in a household's stock."""
    max_keeps_days: int = 60
    """Beyond this, a product is treated as keeping (no due date, never asked "still there?")."""
    ask_grace_days: int = 3
    """Days a "still there?" question may stay unanswered before the entry leaves the stock."""

    def __post_init__(self) -> None:
        for name in ("max_list_items", "max_stock_items", "max_keeps_days"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.ask_grace_days < 0:
            raise ValueError("ask_grace_days must not be negative")


DEFAULT_LIMITS = Limits()
