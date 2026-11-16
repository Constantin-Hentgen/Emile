"""Structured quantities: strict reading, normalised writing, subtraction."""

from fractions import Fraction

import pytest

from emile.core.quantity import IncompatibleQuantity, Quantity, Unit, format_quantity, parse, subtract


@pytest.mark.parametrize(
    ("text", "amount", "unit"),
    [
        ("500 g", 500, Unit.G),
        ("500g", 500, Unit.G),
        ("1,5 kg", 1500, Unit.G),
        ("1.5 kilos", 1500, Unit.G),
        ("250 grammes", 250, Unit.G),
        ("200 mg", Fraction(1, 5), Unit.G),
        ("1 L", 1000, Unit.ML),
        ("75 cl", 750, Unit.ML),
        ("2 dl", 200, Unit.ML),
        ("½ litre", 500, Unit.ML),
        ("1 ½ kg", 1500, Unit.G),
        ("7", 7, Unit.PIECE),
        ("1/2", Fraction(1, 2), Unit.PIECE),
        ("6 pièces", 6, Unit.PIECE),
        ("une douzaine", 12, Unit.PIECE),
        ("a dozen", 12, Unit.PIECE),
        ("3 boîtes", 3, Unit.BOX),
        ("2 boxes", 2, Unit.BOX),
        ("un paquet", 1, Unit.PACK),
        ("3 sachets", 3, Unit.BAG),
        ("2 bouteilles", 2, Unit.BOTTLE),
        ("4 tranches", 4, Unit.SLICE),
        ("2 x 400 g", 800, Unit.G),
        ("2×400g", 800, Unit.G),
        ("2 boîtes de 400 g", 800, Unit.G),
        ("3 packs of 250 g", 750, Unit.G),
        ("  1  KG. ", 1000, Unit.G),
    ],
)
def test_known_forms_are_read_in_base_units(text, amount, unit):
    quantity = parse(text)
    assert quantity is not None
    assert (quantity.amount, quantity.unit) == (Fraction(amount), unit)


@pytest.mark.parametrize(
    "text", [None, "", "un", "un peu", "1 botte", "quelques", "1 000 g", "1/0", "2 boîtes de 3 boîtes", "500 g farine"]
)
def test_anything_else_is_not_guessed(text):
    assert parse(text) is None


@pytest.mark.parametrize(
    ("text", "fr", "en"),
    [
        ("1500 g", "1,5 kg", "1.5 kg"),
        ("1250 g", "1,25 kg", "1.25 kg"),
        ("800 g", "800 g", "800 g"),
        ("2.5 g", "2,5 g", "2.5 g"),
        ("75 cl", "750 ml", "750 ml"),
        ("2000 ml", "2 L", "2 L"),
        ("7", "7", "7"),
        ("1/2", "0,5", "0.5"),
        ("1 boîte", "1 boîte", "1 boîte"),  # a French word stays French
        ("2 boxes", "2 boxes", "2 boxes"),
    ],
)
def test_written_back_in_the_shortest_usual_form(text, fr, en):
    quantity = parse(text)
    assert quantity is not None
    assert (format_quantity(quantity, "fr"), format_quantity(quantity, "en")) == (fr, en)


def test_plural_rules_differ_by_language():
    assert format_quantity(Quantity(Fraction(3, 2), Unit.BOX), "fr") == "1,5 boîte"
    assert format_quantity(Quantity(Fraction(3, 2), Unit.BOX), "en") == "1.5 boxes"
    assert format_quantity(Quantity(Fraction(1), Unit.BOX), "en") == "1 box"


def test_subtract_in_compatible_units():
    left = subtract(parse("1 kg"), parse("200 g"))  # type: ignore[arg-type]
    assert left is not None
    assert format_quantity(left) == "800 g"
    assert subtract(parse("1 kg"), parse("1000 g")) is None  # type: ignore[arg-type]
    assert subtract(parse("500 g"), parse("1 kg")) is None  # type: ignore[arg-type]
    boxes = subtract(parse("3 boîtes"), parse("1 boîte"))  # type: ignore[arg-type]
    assert boxes is not None
    assert format_quantity(boxes, "en") == "2 boîtes"


@pytest.mark.parametrize(("have", "used"), [("3 boîtes", "200 g"), ("1 L", "1 kg"), ("6", "2 boîtes")])
def test_subtract_refuses_different_kinds(have, used):
    with pytest.raises(IncompatibleQuantity):
        subtract(parse(have), parse(used))  # type: ignore[arg-type]


def test_measure_is_what_clients_see():
    assert parse("1,5 kg").measure().model_dump() == {"amount": 1500.0, "unit": "g"}  # type: ignore[union-attr]
