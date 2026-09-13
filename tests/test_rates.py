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


def rated(rid, carrier, amount, currency, days=None):
    r = rate(rid, carrier=carrier, amount=amount, currency=currency)
    r.delivery_days = days
    return r


# The shape of a real US to US quote from an account with Royal Mail connected
# as well: Royal Mail answers in pounds, with one real price and the rest of its
# catalogue billed to the account, and none of it can be bought for this route.
US_ROUTE = [
    rated("rm_48", "RoyalMailV3", "3.25", "GBP", 2),
    rated("rm_billed", "RoyalMailV3", "0.01", "GBP", 1),
    rated("usps_ga", "USPS", "7.04", "USD", 2),
    rated("usps_exp", "USPS", "39.05", "USD", 1),
    rated("fedex_ground", "FedExDefault", "7.01", "USD", 3),
    rated("ups_ground", "UPSDAP", "16.75", "USD", 2),
]


def test_cheapest_never_compares_across_currencies():
    # 3.25 GBP is a smaller number than 7.01 USD, and that is all it is.
    assert rates.cheapest_rate_id(US_ROUTE) == "fedex_ground"


def test_comparison_currency_is_the_majority_of_real_quotes():
    assert rates.comparison_currency(US_ROUTE) == "USD"


def test_the_senders_currency_wins_when_anything_is_quoted_in_it():
    assert rates.comparison_currency(US_ROUTE, preferred="GBP") == "GBP"
    assert rates.cheapest_rate_id(US_ROUTE, preferred="gbp") == "rm_48"


def test_a_preferred_currency_nobody_quoted_in_falls_back_to_the_majority():
    assert rates.comparison_currency(US_ROUTE, preferred="EUR") == "USD"


def test_markers_do_not_vote_for_a_currency():
    # Sixty account-billed Royal Mail rows must not outvote three real quotes.
    offered = [rated(f"rm{i}", "RoyalMailV3", "0.01", "GBP") for i in range(60)]
    offered += [rated("usps", "USPS", "7.04", "USD"), rated("ups", "UPSDAP", "9.00", "USD")]
    assert rates.comparison_currency(offered) == "USD"


def test_comparison_currency_is_none_when_nothing_is_priced():
    assert rates.comparison_currency([rated("rm", "RoyalMail", "0.01", "GBP")]) is None
    assert rates.comparison_currency([]) is None


def test_comparison_currency_tie_is_stable():
    offered = [rated("a", "USPS", "5.00", "USD"), rated("b", "RoyalMailV3", "4.00", "GBP")]
    assert rates.comparison_currency(offered) == "GBP"
    assert rates.comparison_currency(list(reversed(offered))) == "GBP"


def test_an_explicit_currency_is_honoured():
    assert rates.cheapest_rate_id(US_ROUTE, currency="GBP") == "rm_48"


def test_placeholders_are_never_cheapest():
    offered = [rated("ph", "USPS", "0.01", "USD"), rated("real", "USPS", "8.00", "USD")]
    assert rates.cheapest_rate_id(offered) == "real"


def test_fastest_is_judged_among_the_same_rates_as_cheapest():
    # The account-billed Royal Mail row claims one day, as does USPS Express.
    # Only the one that can be bought on this route is recommended.
    assert rates.fastest_rate_id(US_ROUTE) == "usps_exp"


def test_fastest_ignores_rates_with_no_estimate():
    offered = [rated("slow", "USPS", "5", "USD", 5), rated("none", "USPS", "4", "USD"),
               rated("quick", "USPS", "9", "USD", 1)]
    assert rates.fastest_rate_id(offered) == "quick"


def test_fastest_is_none_when_nobody_quoted_days():
    assert rates.fastest_rate_id([rated("a", "USPS", "5", "USD")]) is None
    assert rates.fastest_rate_id([]) is None


@pytest.mark.parametrize("value, expected", [(3, 3), ("2", 2), (None, None), ("soon", None)])
def test_delivery_days(value, expected):
    assert rates.delivery_days(SimpleNamespace(delivery_days=value)) == expected


def test_carriers_that_cannot_quote_in_the_routes_currency_do_not_lead():
    order = rates.order_carriers(US_ROUTE, currency="USD")
    assert order == ["FedExDefault", "USPS", "UPSDAP", "RoyalMailV3"]


def test_carriers_with_only_markers_go_last():
    offered = [rated("rm", "RoyalMailV3", "0.01", "GBP"), rated("evri", "Evri", "3.20", "GBP"),
               rated("dpd", "DPDUK", "6.50", "EUR")]
    assert rates.order_carriers(offered, currency="GBP") == ["Evri", "DPDUK", "RoyalMailV3"]


def test_a_carrier_that_reported_a_problem_sorts_after_one_that_did_not():
    offered = [rated("fx", "FedEx", "5.00", "USD"), rated("ups", "UPSDAP", "9.00", "USD")]
    assert rates.order_carriers(offered, currency="USD", declined=["FedEx"]) == ["UPSDAP", "FedEx"]
