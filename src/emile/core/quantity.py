"""Structured quantities read from free text: ``"500 g"``, ``"1,5 L"``, ``"2 x 400 g"``, ``"3 boîtes"``, ``"7"``.

The text a client sends stays the source of truth; this module only *reads* it. Reading is strict on purpose: the
whole text must match a known form, otherwise :func:`parse` returns ``None`` and nothing is guessed. A wrong guess
would silently corrupt the stock when quantities are subtracted, while an unread quantity only means the client
has to say what is left (``qty_left``) instead of what was used.

Units are normalised to one base unit per kind (grams, millilitres, pieces), so ``1 kg`` minus ``200 g`` works.
Containers (boxes, packs, bags, bottles, slices) are counted on their own: ``3 boîtes`` minus ``1 boîte`` works,
``3 boîtes`` minus ``200 g`` does not.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel

from emile.core.errors import InvalidInput

type Lang = Literal["fr", "en"]


class Unit(StrEnum):
    """Base unit of a quantity. Two quantities can be added or subtracted only if their units are equal."""

    G = "g"
    ML = "ml"
    PIECE = "piece"
    BOX = "box"
    PACK = "pack"
    BAG = "bag"
    BOTTLE = "bottle"
    SLICE = "slice"


class IncompatibleQuantity(InvalidInput):
    """Two quantities of different kinds (``200 g`` from ``3 boîtes``); nothing was changed."""


class Measure(BaseModel):
    """A quantity as clients see it: ``{"amount": 1500.0, "unit": "g"}``."""

    amount: float
    unit: Unit


@dataclass(frozen=True)
class Quantity:
    """An exact amount in a base unit. ``lang`` remembers the language of a word read from the text (``boîtes`` is
    French) so that writing it back keeps that language."""

    amount: Fraction
    unit: Unit
    lang: Lang | None = None

    def measure(self) -> Measure:
        return Measure(amount=round(float(self.amount), 3), unit=self.unit)


# --- reading ----------------------------------------------------------------------------------------------------

# alias → (unit, factor to the base unit, language of the word or None when shared)
_UNITS: dict[str, tuple[Unit, Fraction, Lang | None]] = {
    "mg": (Unit.G, Fraction(1, 1000), None),
    "g": (Unit.G, Fraction(1), None),
    "gr": (Unit.G, Fraction(1), None),
    "kg": (Unit.G, Fraction(1000), None),
    "ml": (Unit.ML, Fraction(1), None),
    "cl": (Unit.ML, Fraction(10), None),
    "dl": (Unit.ML, Fraction(100), None),
    "l": (Unit.ML, Fraction(1000), None),
    "pc": (Unit.PIECE, Fraction(1), None),
    "pcs": (Unit.PIECE, Fraction(1), None),
}
_WORDS: dict[Lang, dict[Unit | str, tuple[str, ...]]] = {
    "fr": {
        "gramme": ("gramme", "grammes"),
        "kilo": ("kilo", "kilos", "kilogramme", "kilogrammes"),
        "litre": ("litre", "litres"),
        Unit.PIECE: ("pièce", "pièces", "piece", "pieces"),
        "dozen": ("douzaine", "douzaines"),
        Unit.BOX: ("boîte", "boîtes", "boite", "boites"),
        Unit.PACK: ("paquet", "paquets"),
        Unit.BAG: ("sachet", "sachets"),
        Unit.BOTTLE: ("bouteille", "bouteilles"),
        Unit.SLICE: ("tranche", "tranches"),
    },
    "en": {
        "gramme": ("gram", "grams"),
        "kilo": ("kilogram", "kilograms"),
        "litre": ("liter", "liters"),
        Unit.PIECE: ("piece", "pieces"),
        "dozen": ("dozen", "dozens"),
        Unit.BOX: ("box", "boxes"),
        Unit.PACK: ("pack", "packs", "packet", "packets"),
        Unit.BAG: ("bag", "bags"),
        Unit.BOTTLE: ("bottle", "bottles"),
        Unit.SLICE: ("slice", "slices"),
    },
}
_WORD_UNITS: dict[str, tuple[Unit, Fraction]] = {
    "gramme": (Unit.G, Fraction(1)),
    "kilo": (Unit.G, Fraction(1000)),
    "litre": (Unit.ML, Fraction(1000)),
    "dozen": (Unit.PIECE, Fraction(12)),
}
for _lang, _table in _WORDS.items():
    for _key, _forms in _table.items():
        _unit, _factor = _WORD_UNITS[_key] if _key in _WORD_UNITS else (Unit(_key), Fraction(1))
        for _form in _forms:
            # A word spelt the same in both languages ("piece") is shared: it carries no language.
            _UNITS[_form] = (_unit, _factor, None if _form in _UNITS else _lang)

_FRACTIONS = {"½": Fraction(1, 2), "¼": Fraction(1, 4), "¾": Fraction(3, 4)}
_ONE = {"un", "une", "a", "an", "one"}

_NUMBER = r"(?:\d+(?:[.,]\d+)?(?:\s*[½¼¾])?|\d+/\d+|[½¼¾]|un|une|an|a|one)"
_UNIT = r"(?:[^\W\d_]+\.?)"
_SIMPLE = re.compile(rf"(?P<n>{_NUMBER})\s*(?P<u>{_UNIT})?")
_TIMES = re.compile(rf"(?P<k>{_NUMBER})\s*[x×*]\s*(?P<rest>.+)")
_OF = re.compile(rf"(?P<k>{_NUMBER})\s*(?P<c>{_UNIT})\s+(?:de|d'|d’|of)\s*(?P<rest>.+)")


def _number(text: str) -> Fraction | None:
    text = text.strip()
    if text in _ONE:
        return Fraction(1)
    if text in _FRACTIONS:
        return _FRACTIONS[text]
    if "/" in text:
        top, bottom = text.split("/")
        return Fraction(int(top), int(bottom)) if int(bottom) else None
    extra = Fraction(0)
    if text[-1] in _FRACTIONS:
        extra, text = _FRACTIONS[text[-1]], text[:-1].strip()
    return Fraction(text.replace(",", ".")) + extra


def _simple(text: str) -> Quantity | None:
    match = _SIMPLE.fullmatch(text)
    if match is None:
        return None
    number = _number(match["n"])
    word = (match["u"] or "").rstrip(".")
    if number is None:
        return None
    if not word:
        if match["n"] in _ONE:  # "un" alone is a word, not a quantity
            return None
        return Quantity(number, Unit.PIECE)
    if word not in _UNITS:
        return None
    unit, factor, lang = _UNITS[word]
    return Quantity(number * factor, unit, lang)


def parse(text: str | None) -> Quantity | None:
    """Read a quantity, or ``None`` if the text is not one of the known forms.

    >>> parse("1,5 kg")
    Quantity(amount=Fraction(1500, 1), unit=<Unit.G: 'g'>, lang=None)
    >>> parse("2 boîtes de 400 g").amount
    Fraction(800, 1)
    >>> parse("un peu") is None
    True
    """
    if not text:
        return None
    text = " ".join(text.split()).casefold().rstrip(".")
    try:
        for pattern in (_OF, _TIMES):
            match = pattern.fullmatch(text)
            if match is None:
                continue
            count = _number(match["k"])
            inner = _simple(match["rest"])
            if pattern is _OF and match["c"].rstrip(".") not in _UNITS:
                continue
            if pattern is _OF and inner is not None and inner.unit is _UNITS[match["c"].rstrip(".")][0]:
                continue  # "2 boîtes de 3 boîtes": not a product
            if count is not None and inner is not None:
                return Quantity(count * inner.amount, inner.unit, inner.lang)
        return _simple(text)
    except (ValueError, ZeroDivisionError):
        return None


# --- writing ----------------------------------------------------------------------------------------------------

_NAMES: dict[Lang, dict[Unit, tuple[str, str]]] = {
    "fr": {
        Unit.BOX: ("boîte", "boîtes"),
        Unit.PACK: ("paquet", "paquets"),
        Unit.BAG: ("sachet", "sachets"),
        Unit.BOTTLE: ("bouteille", "bouteilles"),
        Unit.SLICE: ("tranche", "tranches"),
    },
    "en": {
        Unit.BOX: ("box", "boxes"),
        Unit.PACK: ("pack", "packs"),
        Unit.BAG: ("bag", "bags"),
        Unit.BOTTLE: ("bottle", "bottles"),
        Unit.SLICE: ("slice", "slices"),
    },
}


def _num(value: Fraction, places: int, lang: Lang) -> str:
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if lang == "fr" else text


def format_quantity(quantity: Quantity, lang: Lang = "fr") -> str:
    """Write a quantity back in its shortest usual form: ``1,5 kg``, ``750 ml``, ``2 L``, ``7``, ``2 boîtes``.

    The language of a word read from the original text wins over ``lang``, so ``2 boxes`` stays English.
    """
    lang = quantity.lang or lang
    amount, unit = quantity.amount, quantity.unit
    if unit in (Unit.G, Unit.ML):
        big = "kg" if unit is Unit.G else "L"
        # One decimal of the base unit is kept either way, so writing a quantity back never loses what was used.
        if amount >= 1000:
            return f"{_num(amount / 1000, 4, lang)} {big}"
        return f"{_num(amount, 1, lang)} {unit.value}"
    number = _num(amount, 2, lang)
    if unit is Unit.PIECE:
        return number
    singular, plural = _NAMES[lang][unit]
    many = amount >= 2 if lang == "fr" else amount != 1
    return f"{number} {plural if many else singular}"


def subtract(have: Quantity, used: Quantity) -> Quantity | None:
    """What is left after using ``used`` out of ``have``; ``None`` when nothing is left (all of it, or more, was used).

    Raises :class:`IncompatibleQuantity` for quantities of different kinds.
    """
    if have.unit is not used.unit:
        raise IncompatibleQuantity(f"cannot take {used.unit.value} from {have.unit.value}")
    rest = have.amount - used.amount
    return Quantity(rest, have.unit, have.lang) if rest > 0 else None
