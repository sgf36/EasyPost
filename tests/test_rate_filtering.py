"""Placeholder-rate filtering for the Create Shipment rates table.

Royal Mail V3 (via EasyPost) returns its whole service catalogue as rates,
including services that don't apply to the route, priced at a nominal 0.01 that
cannot actually be purchased. Those must be hidden when genuine quotes exist.
"""

from types import SimpleNamespace

from app.ui.views.create_shipment_view import _is_placeholder_rate


def _rate(amount):
    return SimpleNamespace(rate=amount, currency="GBP")


def test_penny_placeholder_is_filtered():
    assert _is_placeholder_rate(_rate("0.01")) is True
    assert _is_placeholder_rate(_rate("0.00")) is True


def test_real_rates_are_kept():
    assert _is_placeholder_rate(_rate("2.20")) is False
    assert _is_placeholder_rate(_rate("2.40")) is False
    assert _is_placeholder_rate(_rate("0.02")) is False  # at the threshold, kept


def test_unparseable_rate_is_not_treated_as_placeholder():
    # A missing/garbage rate should surface (not be silently hidden) rather than
    # be swallowed as a placeholder.
    assert _is_placeholder_rate(_rate(None)) is False
    assert _is_placeholder_rate(_rate("abc")) is False
