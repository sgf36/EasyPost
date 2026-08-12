"""Placeholder-rate filtering for the Create Shipment rates table.

Royal Mail V3 (via EasyPost) returns its whole service catalogue as rates,
including services that don't apply to the route, priced at a nominal 0.01 that
cannot actually be purchased. Those must be hidden when genuine quotes exist.
"""

from types import SimpleNamespace

from app.ui.views.create_shipment_view import _is_account_billed, _is_placeholder_rate


def _rate(amount):
    return SimpleNamespace(rate=amount, currency="GBP")


def _rm_rate(amount):
    # A Royal Mail v3 rate: an account-billed (OBA) carrier whose sub-penny
    # figure means "billed to account", not a non-purchasable placeholder.
    return SimpleNamespace(rate=amount, currency="GBP", carrier="RoyalMailV3")


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


def test_account_billed_penny_rate_is_not_a_placeholder():
    # Royal Mail bills the real postage to the account, so its 0.01 label IS
    # purchasable and must never be hidden as a placeholder.
    rate = _rm_rate("0.01")
    assert _is_account_billed(rate) is True
    assert _is_placeholder_rate(rate) is False


def test_account_billed_only_applies_below_the_threshold():
    # A real-priced Royal Mail rate is a normal quote, not "billed to account".
    rate = _rm_rate("3.25")
    assert _is_account_billed(rate) is False
    assert _is_placeholder_rate(rate) is False


def test_non_royal_mail_penny_stays_a_placeholder():
    # The account-billed exemption is carrier-specific: a 0.01 from any other
    # carrier is still a non-purchasable catalogue placeholder.
    assert _is_account_billed(_rate("0.01")) is False
    assert _is_placeholder_rate(_rate("0.01")) is True
