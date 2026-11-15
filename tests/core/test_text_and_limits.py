import pytest

from emile.core import Limits, keeps_days
from emile.core.text import clean, name_key, positive_int


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(3, 3), ("5", 5), (2.9, 2), ("7.0", 7), (60, 60), (61, None), (0, None), (-2, None), (None, None), ("soon", None)],
)
def test_keeps_days_keeps_plausible_positive_days_only(raw, expected):
    assert keeps_days(raw) == expected


def test_keeps_days_follows_the_configured_maximum():
    assert keeps_days(90, Limits(max_keeps_days=120)) == 90


def test_clean_collapses_whitespace_caps_length_and_empties_to_none():
    assert clean("  Thon   en\tboîte ", 80) == "Thon en boîte"
    assert clean("x" * 100, 40) == "x" * 40
    assert clean("   ", 10) is None
    assert clean(None, 10) is None
    assert clean(6, 10) == "6"


def test_names_match_case_and_space_insensitively():
    assert name_key("  Œufs  frais") == name_key("œufs frais")


def test_positive_int():
    assert positive_int("7") == 7
    assert positive_int(0) is None
    assert positive_int("a week") is None


@pytest.mark.parametrize("field", ["max_list_items", "max_stock_items", "max_keeps_days"])
def test_limits_reject_nonsense(field):
    with pytest.raises(ValueError, match=field):
        Limits(**{field: 0})
    with pytest.raises(ValueError, match="ask_grace_days"):
        Limits(ask_grace_days=-1)
