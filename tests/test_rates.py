"""What a rate's figure means: a price, a "billed to account" marker, or a
placeholder that cannot be bought.

The spending ceilings for AI agents depend on these rules, so they are pinned
here against the shapes EasyPost actually returns (``rate`` as a string).
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import rates


def rate(rid="rate_1", carrier="USPS", amount="7.04", currency="USD"):
    return SimpleNamespace(id=rid, carrier=carrier, service="Service", rate=amount,
                           currency=currency)


@pytest.mark.parametrize("value, expected", [
    ("7.04", 7.04), (12, 12.0), ("0.01", 0.01),
    (None, None), ("", None), ("free", None), ("nan", None), ("inf", None), ("-inf", None),
])
def test_rate_amount_is_none_when_the_price_cannot_be_read(value, expected):
    # None, never 0: an unreadable price is unknown, not free.
    assert rates.rate_amount(rate(amount=value)) == expected


def test_royal_mail_sub_penny_rate_is_account_billed_not_a_placeholder():
    for carrier in ("RoyalMail", "RoyalMailV3"):
        billed = rate(carrier=carrier, amount="0.01", currency="GBP")
        assert rates.is_account_billed(billed)
        assert not rates.is_placeholder_rate(billed)


def test_royal_mail_with_a_real_price_is_neither():
    priced = rate(carrier="RoyalMailV3", amount="8.95", currency="GBP")
    assert not rates.is_account_billed(priced)
    assert not rates.is_placeholder_rate(priced)


def test_other_carriers_sub_penny_rate_is_a_placeholder():
    placeholder = rate(carrier="USPS", amount="0.01")
    assert rates.is_placeholder_rate(placeholder)
    assert not rates.is_account_billed(placeholder)


def test_threshold_is_exclusive():
    assert not rates.is_placeholder_rate(rate(amount="0.02"))
    assert not rates.is_account_billed(rate(carrier="RoyalMailV3", amount="0.02"))


def test_unreadable_price_is_neither_marker():
    for carrier in ("USPS", "RoyalMailV3"):
        broken = rate(carrier=carrier, amount=None)
        assert not rates.is_account_billed(broken)
        assert not rates.is_placeholder_rate(broken)


def test_cheapest_ignores_account_billed_markers():
    offered = [
        rate("rm", carrier="RoyalMailV3", amount="0.01", currency="GBP"),
        rate("dpd", carrier="DPDUK", amount="6.50", currency="GBP"),
        rate("evri", carrier="Evri", amount="3.20", currency="GBP"),
    ]
    assert rates.cheapest_rate_id(offered) == "evri"


def test_cheapest_is_none_when_nothing_is_priced():
    assert rates.cheapest_rate_id([]) is None
    assert rates.cheapest_rate_id([rate("rm", carrier="RoyalMail", amount="0.01")]) is None


def test_unreadable_price_sorts_last_rather_than_raising():
    offered = [rate("broken", amount="n/a"), rate("real", amount="9.00")]
    assert rates.cheapest_rate_id(offered) == "real"


def test_module_does_not_import_qt():
    """The agent path runs in the MCP server process, which has no Qt."""
    code = (
        "import sys, app.services.rates; "
        "sys.exit(1 if any(m.startswith('PySide6') for m in sys.modules) else 0)"
    )
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root)
    assert result.returncode == 0, result.stderr


# The Create Shipment view still carries private copies of these rules; a
# follow-up switches it to app.services.rates. Until then this stops the two
# drifting apart unnoticed.
PARITY_CASES = [
    rate("a", "USPS", "7.04"), rate("b", "USPS", "0.01"), rate("c", "RoyalMailV3", "0.01"),
    rate("d", "RoyalMail", "0.019"), rate("e", "RoyalMailV3", "5.10"), rate("f", "UPS", None),
    rate("g", "FedEx", "abc"), rate("h", "DHL", "0.02"), rate("i", "RoyalMail", ""),
]


def test_view_private_copies_agree_with_the_service():
    view = pytest.importorskip("app.ui.views.create_shipment_view")
    if not hasattr(view, "_is_account_billed"):
        pytest.skip("the view now uses app.services.rates; delete this test")
    for case in PARITY_CASES:
        assert view._is_account_billed(case) == rates.is_account_billed(case), case
        assert view._is_placeholder_rate(case) == rates.is_placeholder_rate(case), case
    assert view._cheapest_rate_id(PARITY_CASES) == rates.cheapest_rate_id(PARITY_CASES)
    assert view._MIN_REAL_RATE == rates.MIN_REAL_RATE
    assert set(view._ACCOUNT_BILLED_CARRIERS) == set(rates.ACCOUNT_BILLED_CARRIERS)
