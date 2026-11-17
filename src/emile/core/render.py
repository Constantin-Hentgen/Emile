"""Deterministic text for clients: checklists, stock summaries for a prompt or a chat, cost ranges.

Everything a person may read exists in French and English. Strings are kept here, next to the code that uses them,
rather than in gettext catalogues: there are few of them, and a library user should not need a build step.
"""

import datetime
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal
from zoneinfo import ZoneInfo

from emile.core.models import ListItem, ShoppingList, StockEntry
from emile.core.stock import boxes_in_use
from emile.core.text import name_key

type Lang = Literal["fr", "en"]
LANGS: tuple[Lang, ...] = ("fr", "en")

_DAYS = {
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_TEXT: dict[str, dict[Lang, str]] = {
    "misc_aisle": {"fr": "🛒 Divers", "en": "🛒 Misc"},
    "days": {"fr": "{n} jours", "en": "{n} days"},
    "day": {"fr": "{n} jour", "en": "{n} day"},
    "estimated": {"fr": "{cost} estimés", "en": "{cost} estimated"},
    "empty_stock": {"fr": "vide", "en": "empty"},
    "today_short": {"fr": " · aujourd'hui", "en": " · today"},
    "ago_short": {"fr": " · il y a {n} j", "en": " · {n} d ago"},
    "today_long": {"fr": " — acheté aujourd'hui", "en": " — bought today"},
    "ago_long": {"fr": " — acheté il y a {n} j", "en": " — bought {n} d ago"},
    "asked": {"fr": " — à vérifier (« encore là ? » envoyé)", "en": ' — to check ("still there?" sent)'},
    "overdue": {"fr": " — date dépassée", "en": " — past its date"},
    "due_today": {"fr": " — à consommer aujourd'hui", "en": " — use today"},
    "keeps_until": {"fr": " — se garde jusqu'au {day}", "en": " — keeps until {day}"},
    "boxes": {
        "fr": "{used}/{total} occupées{detail} → {free} libre(s)",
        "en": "{used}/{total} in use{detail} → {free} free",
    },
    "list_none": {"fr": "aucune", "en": "none"},
    "list_meta": {"fr": "{n} articles, {done} achetés", "en": "{n} items, {done} bought"},
    "list_covers": {"fr": "couvre {n} jours", "en": "covers {n} days"},
    "list_event": {"fr": "dans l'agenda le {day} {time}", "en": "in the calendar on {day} {time}"},
    "list_legend": {"fr": "✅ = acheté", "en": "✅ = bought"},
    "event_title": {"fr": "🛒 Courses ({details})", "en": "🛒 Shopping ({details})"},
    "event_items": {"fr": "{n} articles", "en": "{n} items"},
    "event_covers": {"fr": "Couvre : {what}", "en": "Covers: {what}"},
    "event_cost": {"fr": "Coût estimé : {cost}", "en": "Estimated cost: {cost}"},
    "event_recipes": {"fr": "🍳 RECETTES", "en": "🍳 RECIPES"},
}


def text(key: str, lang: Lang, **values: object) -> str:
    """A translated string; unknown languages fall back to English."""
    variants = _TEXT[key]
    return variants.get(lang, variants["en"]).format(**values)


def day_label(day: datetime.date, lang: Lang) -> str:
    """Short day for a due date: "lun. 16/11" / "Mon 16 Nov"."""
    weekday = _DAYS.get(lang, _DAYS["en"])[day.weekday()]
    if lang == "fr":
        return f"{weekday} {day:%d/%m}"
    return f"{weekday} {day.day} {_MONTHS_EN[day.month - 1]}"


# --- shopping list --------------------------------------------------------------------------------------------


def item_label(item: ListItem) -> str:
    """ "Bananas — 7", or just "Bananas"."""
    return item.name + (f" — {item.qty}" if item.qty else "")


def group_by_aisle(
    items: Sequence[ListItem], lang: Lang = "fr", order: Mapping[str, float] | None = None
) -> dict[str, list[tuple[int, ListItem]]]:
    """Aisle → ``[(index, item)]``, aisles in order of first appearance (the order the client chose).

    ``order`` (aisle key → score, see :mod:`emile.core.aisles`) puts the aisles of a known shop first, in the order
    that shop is laid out; the others follow in the client's order.

    Indexes are positions in the list, which is what toggling takes.
    """
    groups: dict[str, list[tuple[int, ListItem]]] = {}
    for n, item in enumerate(items):
        groups.setdefault(item.aisle or text("misc_aisle", lang), []).append((n, item))
    if order:
        known = sorted((a for a in groups if name_key(a) in order), key=lambda a: order[name_key(a)])
        groups = {a: groups[a] for a in [*known, *(a for a in groups if a not in known)]}
    return groups


def format_cost(cost_min: float | None, cost_max: float | None, lang: Lang = "fr", currency: str = "€") -> str | None:
    """ "55–70 €" for a range, "~12 €" for a single figure (whole units: these are estimates); ``None`` if unknown."""
    if cost_min is None and cost_max is None:
        return None
    if cost_min is None or cost_max is None or round(cost_min) == round(cost_max):
        amount = f"{round(cost_min if cost_min is not None else cost_max)}"  # type: ignore[arg-type]
        return f"~{amount} {currency}" if lang == "fr" else f"~{currency}{amount}"
    span = f"{round(cost_min)}–{round(cost_max)}"
    return f"{span} {currency}" if lang == "fr" else f"{currency}{span}"


def summary_line(lst: ShoppingList, lang: Lang = "fr", currency: str = "€") -> str:
    """ "7 jours · 55–70 € estimés": what the list covers and what it should cost."""
    parts = []
    if lst.days:
        parts.append(text("days" if lst.days > 1 else "day", lang, n=lst.days))
    if cost := format_cost(lst.cost_min, lst.cost_max, lang, currency):
        parts.append(text("estimated", lang, cost=cost))
    return " · ".join(parts)


def checklist(items: Sequence[ListItem], lang: Lang = "fr", order: Mapping[str, float] | None = None) -> str:
    """Aisle-grouped checklist (✅ bought, ☐ to buy), e.g. for a calendar description or a plain-text message."""
    return "\n\n".join(
        f"{aisle}\n" + "\n".join(f"{'✅' if item.done else '☐'} {item_label(item)}" for __, item in entries)
        for aisle, entries in group_by_aisle(items, lang, order).items()
    )


def event_summary(lst: ShoppingList, lang: Lang = "fr", currency: str = "€") -> str:
    """Title of the calendar event: "🛒 Courses (12 articles, 7 jours · 55–70 € estimés)"."""
    details = ", ".join(
        x for x in (text("event_items", lang, n=len(lst.items)), summary_line(lst, lang, currency)) if x
    )
    return text("event_title", lang, details=details)


def event_description(
    lst: ShoppingList, lang: Lang = "fr", currency: str = "€", order: Mapping[str, float] | None = None
) -> str:
    """Description of the calendar event: coverage and estimated cost, the aisle-grouped checklist, then recipes."""
    head = []
    if lst.days or lst.coverage:
        what = ", ".join(x for x in (lst.days and text("days", lang, n=lst.days), lst.coverage) if x)
        head.append(text("event_covers", lang, what=what))
    if cost := format_cost(lst.cost_min, lst.cost_max, lang, currency):
        head.append(text("event_cost", lang, cost=cost))
    body = checklist(lst.items, lang, order)
    out = ("\n".join(head) + "\n\n" if head else "") + body
    if lst.recipes:
        out += f"\n\n{text('event_recipes', lang)}\n\n{lst.recipes.strip()}"
    return out


def list_text(lst: ShoppingList | None, lang: Lang = "fr", tz: ZoneInfo | None = None) -> str:
    """The list in one block for a language model's context: counts, coverage, slot, then one line per item."""
    if lst is None:
        return text("list_none", lang)
    meta = [text("list_meta", lang, n=len(lst.items), done=sum(i.done for i in lst.items))]
    if lst.days:
        meta.append(text("list_covers", lang, n=lst.days))
    if lst.event_start:
        start = lst.event_start.astimezone(tz) if tz else lst.event_start
        meta.append(text("list_event", lang, day=day_label(start.date(), lang), time=f"{start:%H:%M}"))
    lines = [f"({', '.join(meta)}) — {text('list_legend', lang)}:"]
    lines += [
        f"- {'✅' if i.done else '☐'} {i.name}"
        + (f" ({i.qty})" if i.qty else "")
        + (f" [{i.aisle}]" if i.aisle else "")
        for i in lst.items
    ]
    return "\n".join(lines)


# --- stock ----------------------------------------------------------------------------------------------------


def due_label(entry: StockEntry, today: datetime.date, lang: Lang = "fr") -> str:
    """Where a perishable stands: keeps until…, use today, past its date, or waiting for "still there?"."""
    if entry.due is None:
        return ""
    if entry.asked is not None:
        return text("asked", lang)
    if entry.due < today:
        return text("overdue", lang)
    if entry.due == today:
        return text("due_today", lang)
    return text("keeps_until", lang, day=day_label(entry.due, lang))


def stock_text(stock: Iterable[StockEntry], today: datetime.date, lang: Lang = "fr", *, compact: bool = True) -> str:
    """One line per entry, oldest first: name, quantity, age, due label.

    ``compact`` ("· 3 d ago") suits a chat reply; the long form ("— bought 3 d ago") reads better in a prompt.
    """
    entries = sorted(stock, key=lambda e: e.added)
    if not entries:
        return text("empty_stock", lang)
    lines = []
    for e in entries:
        days = (today - e.added).days
        if compact:
            age = text("today_short", lang) if days == 0 else text("ago_short", lang, n=days)
        else:
            age = text("today_long", lang) if days == 0 else text("ago_long", lang, n=days)
        lines.append(f"- {e.name}" + (f" ({e.qty})" if e.qty else "") + age + due_label(e, today, lang))
    return "\n".join(lines)


def boxes_text(stock: Iterable[StockEntry], total: int, lang: Lang = "fr") -> str:
    """ "2/3 occupées (Curry ×2) → 1 libre(s)": how many meal-prep boxes are taken, by what."""
    used = boxes_in_use(stock)
    n = sum(k for __, k in used)
    detail = f" ({', '.join(f'{name} ×{k}' for name, k in used)})" if used else ""
    return text("boxes", lang, used=n, total=total, detail=detail, free=max(0, total - n))
