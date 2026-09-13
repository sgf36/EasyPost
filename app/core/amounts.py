"""Reading a number that a person typed, or that a spreadsheet holds.

This exists because every text field that took an amount read it with some
variation of ``float(text.replace(",", ""))``. That treats a comma as a
thousands separator in every language, while the German and French prompts
show "100,00" as their own example. A German user insuring a 45 euro item typed
"45,00", and the app insured 4,500 US dollars at a hundred times the fee.

The two separators cannot simply be swapped per language either. "1,234" is
one thousand two hundred and thirty-four in English and one point two three
four in German, and the characters alone do not say which the person meant.
So the rules are:

* **Anything the characters settle is accepted, in any language.** A number can
  only have one decimal separator, and grouping never follows it, so "1.234,56"
  and "1,234.56" each have exactly one reading. A separator followed by one,
  two or four digits cannot be grouping, so "45,00" is 45 in English too. That
  matters because the interface language is not the person's region: a German
  living in London may well run the app in English.
* **The one shape the characters do not settle** is a single separator followed
  by exactly three digits. That is resolved by the active language only when
  the language names that exact character. When the reading it gives is
  impossible anyway (money has no third decimal place), or when the language
  uses neither character that way, the input is **refused** with a message
  saying why. Guessing wrong here costs a thousandfold error in money, which is
  worse than asking the user to type "1234".

A file is not in the interface language: the same spreadsheet can be opened by
anyone, and CSV exports almost always use a dot. So a file is read with the dot
as its decimal separator and **no** grouping separator. "12,50" is still 12.50,
because two digits cannot be grouping, but "1,234" is refused as ambiguous.

Results are :class:`~decimal.Decimal`, so an amount of money never passes
through binary floating point on its way to EasyPost.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Optional

from PySide6.QtCore import QLocale

# Characters that only ever group digits. Space variants are what French,
# Russian, Polish, Swedish and others actually use (QLocale reports a narrow or
# ordinary no-break space), and people type a plain space for them.
_GROUPING_ONLY = {" ", "\u00a0", "\u202f", "\u2009", "\u066c"}
# The Arabic decimal separator, which is never used for grouping.
_DECIMAL_ONLY = {"\u066b"}
_MINUS = {"-", "\u2212"}

# Internal markers once the text has been normalised.
_GROUP = "G"
_DECIMAL = "D"

# Western grouping (1,234,567) and the Indian lakh grouping (12,34,567) that
# Hindi, Bengali, Marathi and others use. Anything else is not grouping at all,
# so "12,34" is refused rather than read as 1234.
_GROUPED_WESTERN = re.compile(r"^\d{1,3}(?:X\d{3})+$")
_GROUPED_INDIAN = re.compile(r"^\d{1,2}(?:X\d{2})*X\d{3}$")


class AmountError(ValueError):
    """The text is not a number this module is willing to read.

    ``kind`` is a stable identifier (``not_a_number``, ``ambiguous``,
    ``too_many_decimals``, and ``not_positive`` for callers that need more than
    zero) so callers with their own phrasing, such as the batch
    preview that names the spreadsheet column, can map it to their own message.
    ``str()`` of the error is already translated for everyone else.
    """

    def __init__(self, kind: str, text: str, places: Optional[int] = None) -> None:
        from app.i18n import tr

        self.kind = kind
        self.text = text
        if kind == "too_many_decimals":
            message = tr("amount_errors.too_many_decimals", value=text, places=places)
        else:
            message = tr(f"amount_errors.{kind}", value=text)
        super().__init__(message)


def separators_for(locale_code: str) -> tuple[Optional[str], Optional[str]]:
    """The ASCII decimal and grouping separators a language uses, or None.

    Taken from Qt's CLDR data rather than a hand-kept table. None means the
    language uses neither "." nor "," for that role: Arabic and Persian have a
    decimal separator of their own, and Russian groups with a space, so in
    those languages a lone "." or "," followed by three digits says nothing
    about which was meant.
    """
    qlocale = QLocale(locale_code)
    decimal = qlocale.decimalPoint()
    grouping = qlocale.groupSeparator()
    return (
        decimal if decimal in (".", ",") else None,
        grouping if grouping in (".", ",") else None,
    )


def parse_amount(
    value,
    *,
    decimal: Optional[str],
    grouping: Optional[str],
    max_decimals: Optional[int] = None,
) -> Decimal:
    """Read ``value`` as a Decimal, or raise :class:`AmountError`.

    ``decimal`` and ``grouping`` are the separators to trust for the one
    ambiguous shape described in the module docstring. A leading "$" is
    allowed (insurance is always in dollars) and a leading minus sign is kept,
    so callers can say "must be greater than zero" rather than "not a number".
    """
    if isinstance(value, bool) or value is None:
        raise AmountError("not_a_number", "" if value is None else str(value))
    # Values that are already numbers, from code or an Excel cell, have no
    # separators to misread.
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        # repr is the shortest text that round-trips, so 12.5 becomes "12.5"
        # rather than the binary expansion Decimal(12.5) would carry.
        number = Decimal(repr(value))
    else:
        return _parse_text(str(value), decimal, grouping, max_decimals)

    if not number.is_finite():
        raise AmountError("not_a_number", str(value))
    _check_places(number, str(value), max_decimals)
    return number


def parse_typed_amount(value, *, max_decimals: Optional[int] = 2) -> Decimal:
    """Read an amount typed into the interface, in the active language."""
    from app.i18n import current_locale

    decimal, grouping = separators_for(current_locale())
    return parse_amount(value, decimal=decimal, grouping=grouping, max_decimals=max_decimals)


def parse_file_number(value, *, max_decimals: Optional[int] = None) -> Decimal:
    """Read a number from an imported file: dot decimal, no trusted grouping."""
    return parse_amount(value, decimal=".", grouping=None, max_decimals=max_decimals)


def _parse_text(
    original: str, decimal: Optional[str], grouping: Optional[str], max_decimals: Optional[int]
) -> Decimal:
    text = original.strip()
    negative = False
    if text[:1] in _MINUS:
        negative, text = True, text[1:].strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if text[:1] in _MINUS and not negative:
        negative, text = True, text[1:].strip()
    if not text:
        raise AmountError("not_a_number", original.strip())

    normalised = []
    for char in text:
        digit = unicodedata.decimal(char, None)
        if digit is not None:
            # Arabic-Indic, Devanagari and other native digits read as the
            # same number; refusing them would be refusing the language.
            normalised.append(str(digit))
        elif char in (".", ","):
            normalised.append(char)
        elif char in _GROUPING_ONLY:
            normalised.append(_GROUP)
        elif char in _DECIMAL_ONLY:
            normalised.append(_DECIMAL)
        else:
            # Letters, exponents, "nan", "inf", a second sign, other currency
            # symbols: none of them belong in an amount.
            raise AmountError("not_a_number", original.strip())
    s = "".join(normalised)
    if not s[0].isdigit() or not s[-1].isdigit():
        raise AmountError("not_a_number", original.strip())

    point = _decimal_position(s, original.strip(), decimal, grouping, max_decimals)
    whole, fraction = (s, "") if point is None else (s[:point], s[point + 1:])

    if not fraction.isdigit() and fraction:
        raise AmountError("not_a_number", original.strip())
    whole = _ungroup(whole, original.strip())

    try:
        number = Decimal(f"{whole}.{fraction}" if fraction else whole)
    except InvalidOperation:
        raise AmountError("not_a_number", original.strip()) from None
    _check_places(number, original.strip(), max_decimals)
    return -number if negative else number


def _decimal_position(
    s: str, shown: str, decimal: Optional[str], grouping: Optional[str], max_decimals: Optional[int]
) -> Optional[int]:
    """Index of the decimal separator in the normalised text, or None."""
    if _DECIMAL in s:
        if s.count(_DECIMAL) > 1:
            raise AmountError("not_a_number", shown)
        return s.index(_DECIMAL)

    kinds = {c for c in s if c in ".,"}
    if len(kinds) == 2:
        # Both present: the last one is the decimal separator, and it may only
        # appear once. The other is checked as grouping by _ungroup.
        last = max(s.rfind("."), s.rfind(","))
        if s.count(s[last]) > 1:
            raise AmountError("not_a_number", shown)
        return last
    if not kinds:
        return None

    char = kinds.pop()
    if s.count(char) > 1:
        return None  # "1,234,567": a number has only one decimal separator
    position = s.index(char)
    tail = s[position + 1:]
    if len(tail) != 3 or not tail.isdigit():
        return position  # anything but three digits after it cannot be grouping

    # The ambiguous shape: a single separator and exactly three digits after it.
    if char == decimal:
        if max_decimals is not None and max_decimals < 3:
            # The decimal reading is impossible and the grouping reading is not
            # what this language means by the character. Neither is safe.
            raise AmountError("ambiguous", shown)
        return position
    if char == grouping:
        return None
    raise AmountError("ambiguous", shown)


def _ungroup(whole: str, shown: str) -> str:
    """Remove grouping separators, refusing any that do not group properly."""
    marks = {c for c in whole if not c.isdigit()}
    if not marks:
        return whole
    if len(marks) > 1:
        raise AmountError("not_a_number", shown)
    pattern = whole.replace(marks.pop(), "X")
    if not (_GROUPED_WESTERN.match(pattern) or _GROUPED_INDIAN.match(pattern)):
        raise AmountError("not_a_number", shown)
    return pattern.replace("X", "")


def _check_places(number: Decimal, shown: str, max_decimals: Optional[int]) -> None:
    if max_decimals is None:
        return
    exponent = number.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > max_decimals:
        raise AmountError("too_many_decimals", shown, places=max_decimals)
