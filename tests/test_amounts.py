"""Reading typed and imported amounts without misreading the separators.

The defect this pins: insurance read an amount with
``float(text.replace(",", ""))``, while the German and French prompts show
"100,00" as their example. "45,00" insured 4,500 US dollars instead of 45, at a
hundred times the fee, and "1.234,56" insured 1.23.
"""

import json
import re
from decimal import Decimal

import pytest

import app.i18n
from app.core.amounts import (
    AmountError,
    parse_amount,
    parse_file_number,
    parse_typed_amount,
    separators_for,
)

ENGLISH = dict(decimal=".", grouping=",")
GERMAN = dict(decimal=",", grouping=".")
RUSSIAN = dict(decimal=",", grouping=None)  # groups with a no-break space
ARABIC = dict(decimal=None, grouping=None)  # its own separators, U+066B/U+066C


@pytest.fixture
def locale(monkeypatch):
    def use(code):
        monkeypatch.setattr(app.i18n, "current_locale", lambda: code)
    return use


def test_separators_come_from_the_language():
    assert separators_for("en") == (".", ",")
    assert separators_for("de") == (",", ".")
    assert separators_for("fr") == (",", None)
    assert separators_for("hi") == (".", ",")


# ---------------------------------------------------------------------------
# Shapes the characters settle on their own, in any language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("separators", [ENGLISH, GERMAN, RUSSIAN, ARABIC])
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("45,00", "45.00"),          # the reported defect
        ("45.00", "45.00"),
        ("12,5", "12.5"),
        ("100", "100"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1,234,567", "1234567"),
        ("1.234.567", "1234567"),
        ("1 234,56", "1234.56"),
        ("1\u202f234,56", "1234.56"),  # French narrow no-break space
        ("1\u00a0234.5", "1234.5"),
        ("12,34,567.00", "1234567.00"),  # Indian lakh grouping
        ("$250.00", "250.00"),
        ("  99,99 ", "99.99"),
        ("\u0664\u0665\u066b\u0660\u0660", "45.00"),  # Arabic-Indic digits
        ("-5", "-5"),
    ],
)
def test_unambiguous_text_reads_the_same_everywhere(separators, given, expected):
    assert parse_amount(given, **separators) == Decimal(expected)


@pytest.mark.parametrize(
    "given",
    ["", "   ", "abc", "nan", "inf", "1e3", "1,2,3", "1.234.5,6.7",
     "1,23,4", "--5", ",5", "5,", "€5", "1 2", "12.50.00", "1,234 567"],
)
def test_nonsense_is_refused(given):
    with pytest.raises(AmountError) as excinfo:
        parse_amount(given, **ENGLISH)
    assert excinfo.value.kind == "not_a_number"


def test_numbers_that_are_not_text_have_nothing_to_misread():
    assert parse_amount(5000, **GERMAN) == Decimal("5000")
    assert parse_amount(12.5, **GERMAN) == Decimal("12.5")
    assert parse_amount(Decimal("7.25"), **GERMAN) == Decimal("7.25")
    for bad in (None, True, float("nan"), float("inf")):
        with pytest.raises(AmountError):
            parse_amount(bad, **ENGLISH)


# ---------------------------------------------------------------------------
# The one ambiguous shape: a single separator and exactly three digits
# ---------------------------------------------------------------------------


def test_grouping_the_language_uses_is_trusted():
    assert parse_amount("1,234", max_decimals=2, **ENGLISH) == Decimal("1234")
    assert parse_amount("1.234", max_decimals=2, **GERMAN) == Decimal("1234")


@pytest.mark.parametrize(
    ("given", "separators"),
    [
        # The language's decimal separator, but money has no third place.
        ("1,234", GERMAN),
        ("1.234", ENGLISH),
        # The language uses neither character for grouping.
        ("1.234", RUSSIAN),
        ("1,234", ARABIC),
        ("1.234", ARABIC),
    ],
)
def test_money_that_could_be_either_reading_is_refused(given, separators):
    with pytest.raises(AmountError) as excinfo:
        parse_amount(given, max_decimals=2, **separators)
    assert excinfo.value.kind == "ambiguous"
    assert given in str(excinfo.value)


def test_three_decimals_are_read_as_decimals_where_they_are_allowed():
    assert parse_amount("1,234", max_decimals=3, **GERMAN) == Decimal("1.234")
    assert parse_amount("1.234", **ENGLISH) == Decimal("1.234")


def test_money_with_too_many_decimal_places_is_refused():
    with pytest.raises(AmountError) as excinfo:
        parse_amount("12.3456", max_decimals=2, **ENGLISH)
    assert excinfo.value.kind == "too_many_decimals"


# ---------------------------------------------------------------------------
# Typed amounts follow the active language; files do not
# ---------------------------------------------------------------------------


def test_typed_amounts_follow_the_active_language(locale):
    locale("de")
    assert parse_typed_amount("45,00") == Decimal("45.00")
    assert parse_typed_amount("1.234,56") == Decimal("1234.56")
    with pytest.raises(AmountError):
        parse_typed_amount("1,234")
    locale("en")
    assert parse_typed_amount("45,00") == Decimal("45.00")
    assert parse_typed_amount("1,234") == Decimal("1234")


@pytest.mark.parametrize(
    ("given", "expected"),
    [("12.50", "12.50"), ("12,50", "12.50"), ("1.234", "1.234"),
     ("1,234.5", "1234.5"), ("1.234,5", "1234.5"), ("3", "3")],
)
def test_file_numbers_take_a_dot_always_and_a_comma_when_unambiguous(given, expected):
    assert parse_file_number(given) == Decimal(expected)


def test_a_file_comma_before_three_digits_is_refused_whatever_the_language(locale):
    for code in ("en", "de"):
        locale(code)
        with pytest.raises(AmountError) as excinfo:
            parse_file_number("1,234")
        assert excinfo.value.kind == "ambiguous"


def test_every_catalogues_own_example_amount_reads_as_one_hundred():
    """The prompts show an example in the language's own style ("100,00",
    "100.00"). Whatever a catalogue says, typing its example must work."""
    locales = app.i18n.LOCALES_DIR
    for code, _name, _native in app.i18n.SUPPORTED_LOCALES:
        catalogue = json.loads((locales / f"{code}.json").read_text(encoding="utf-8"))
        example = re.search(r"\d[\d.,]*\d", catalogue["claims.amount_placeholder"]).group(0)
        decimal, grouping = separators_for(code)
        assert parse_amount(
            example, decimal=decimal, grouping=grouping, max_decimals=2
        ) == Decimal("100"), code
