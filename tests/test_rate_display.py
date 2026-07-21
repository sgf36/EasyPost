"""Rate ordering/formatting helpers and carrier chip colour assignment.

These are the pure parts of the rates table, so they're testable without a
QApplication — the widget assembly itself is covered by the offscreen GUI
smoke pass.
"""

from types import SimpleNamespace

from app.ui.views.create_shipment_view import (
    _fastest_rate_id,
    _format_delivery,
    _format_price,
    _rate_sort_key,
)
from app.ui.widgets.chips import carrier_colors


def rate(rid, carrier="USPS", amount="10.00", currency="USD", days=None):
    return SimpleNamespace(
        id=rid, carrier=carrier, service="Svc", rate=amount,
        currency=currency, delivery_days=days,
    )


def test_rates_sort_cheapest_first():
    rates = [rate("b", amount="20.13"), rate("a", amount="10.97"), rate("c", amount="67.21")]
    assert [r.id for r in sorted(rates, key=_rate_sort_key)] == ["a", "b", "c"]


def test_unparseable_rate_sorts_last_rather_than_crashing():
    # EasyPost hands back `rate` as a string; a null or junk value must not
    # take the whole table down with it.
    rates = [rate("junk", amount="n/a"), rate("null", amount=None), rate("real", amount="5.00")]
    ordered = sorted(rates, key=_rate_sort_key)
    assert ordered[0].id == "real"
    assert {r.id for r in ordered[1:]} == {"junk", "null"}


def test_sort_is_stable_for_equal_prices():
    rates = [rate("first", amount="10.97"), rate("second", amount="10.97")]
    assert [r.id for r in sorted(rates, key=_rate_sort_key)] == ["first", "second"]


def test_fastest_rate_ignores_rates_with_no_estimate():
    rates = [rate("slow", days=5), rate("none", days=None), rate("quick", days=1)]
    assert _fastest_rate_id(rates) == "quick"


def test_fastest_rate_is_none_when_nobody_quoted_days():
    assert _fastest_rate_id([rate("a"), rate("b")]) is None


def test_fastest_rate_handles_empty_list():
    assert _fastest_rate_id([]) is None


def test_price_folds_currency_into_one_cell():
    assert _format_price(rate("a", amount="10.97", currency="GBP")) == "10.97 GBP"


def test_delivery_shows_bare_number_under_the_est_days_header():
    # A bare number, not "1 days" — the column header carries the unit, which
    # avoids plural rules across all 50 locales.
    assert _format_delivery(rate("a", days=3)) == "3"
    assert _format_delivery(rate("one", days=1)) == "1"
    assert _format_delivery(rate("b", days=None)) == "Not quoted"


def test_carrier_colors_are_stable_and_distinct_for_major_carriers():
    majors = ["USPS", "UPS", "FedEx", "DHLExpress"]
    colors = [carrier_colors(c) for c in majors]
    assert len(set(colors)) == len(majors), "major carriers must be visually distinguishable"
    # Stable across calls and insensitive to case/punctuation.
    assert carrier_colors("USPS") == carrier_colors("usps") == carrier_colors("U.S.P.S.")


def test_unknown_carrier_gets_a_stable_palette_entry():
    first = carrier_colors("SomeRegionalCourier")
    assert first == carrier_colors("SomeRegionalCourier")


def test_blank_carrier_falls_back_to_neutral():
    assert carrier_colors("") == carrier_colors(None)
