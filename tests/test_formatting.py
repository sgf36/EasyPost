"""Every case here reached a published store listing at least once.

The defect class is one thing: an API value printed at a user without passing
through a display function. It kept coming back because the display functions
lived as private methods on CreateShipmentView, so each new view started from
the raw field again.
"""

import json

import pytest

from app.i18n import LOCALES_DIR
from app.services.formatting import (
    _STATUS_KEYS,
    display_carrier,
    display_service,
    display_status,
    format_money,
    format_money_map,
    humanize_code,
)


class TestHumanizeCode:
    def test_splits_camel_case(self):
        assert humanize_code("RoyalMailV3") == "Royal Mail V3"
        assert (
            humanize_code("InternationalBusinessParcelsTracked30kg")
            == "International Business Parcels Tracked 30kg"
        )

    def test_keeps_an_acronym_whole_before_a_word(self):
        # The alternative that a naive camel splitter drops, turning this into
        # "U S P S Priority".
        assert humanize_code("USPSPriority") == "USPS Priority"

    def test_spaces_shouted_names_and_restores_brand_case(self):
        assert humanize_code("FEDEX_GROUND") == "FedEx Ground"
        assert humanize_code("UPS_NEXT_DAY_AIR") == "UPS Next Day Air"
        # Three letters counts: FEDEX_2_DAY was leaving "DAY" shouting.
        assert humanize_code("FEDEX_2_DAY") == "FedEx 2 Day"

    def test_is_idempotent(self):
        # Applied twice by two layers, it must not re-split its own output.
        once = humanize_code("FEDEX_GROUND")
        assert humanize_code(once) == once

    def test_is_total(self):
        assert humanize_code("") == ""
        assert humanize_code("x") == "x"


class TestDisplayCarrier:
    def test_a_carrier_whose_display_name_is_its_code_is_not_split(self):
        # "Fed Ex" reached a published screenshot because the caller inferred
        # "unrecognised" by comparing the resolved name with the input.
        assert display_carrier("FedEx") == "FedEx"
        assert display_carrier("USPS") == "USPS"

    def test_humanises_a_carrier_the_catalogue_does_not_know(self):
        assert display_carrier("SomeNewCarrier") == "Some New Carrier"

    def test_blank_is_configurable(self):
        assert display_carrier("") == ""
        assert display_carrier("", blank="—") == "—"


class TestDisplayService:
    def test_never_returns_a_raw_code(self):
        assert display_service("FEDEX_GROUND") == "FedEx Ground"
        assert display_service("RoyalMail2ndClass") == "Royal Mail 2nd Class"


class TestDisplayStatus:
    def test_translates_rather_than_printing_the_api_value(self):
        assert display_status("in_transit") == "In transit"
        assert display_status("out_for_delivery") == "Out for delivery"
        assert display_status("purchased") == "Purchased"

    def test_drops_a_detail_that_says_nothing(self):
        # "unknown" is what EasyPost sends whenever a carrier has not
        # elaborated, which is most of the time.
        assert display_status("in_transit", "unknown") == "In transit"
        assert display_status("in_transit", "UNKNOWN") == "In transit"
        assert display_status("in_transit", "") == "In transit"
        assert display_status("in_transit", "   ") == "In transit"

    def test_drops_a_detail_that_merely_restates_the_status(self):
        assert display_status("delivered", "delivered") == "Delivered"
        assert display_status("delivered", "Delivered") == "Delivered"

    def test_keeps_a_detail_that_says_something(self):
        # A bare "failure" says a parcel is stuck without saying why. The
        # detail trails after a dash, so it stays lower case.
        assert display_status("failure", "address_incorrect") == "Failed — address incorrect"

    def test_an_unmapped_status_is_humanised_not_printed_raw(self):
        assert display_status("some_new_state") == "Some new state"

    def test_blank_is_configurable(self):
        assert display_status(None) == ""
        assert display_status(None, blank="—") == "—"


class TestMoney:
    def test_an_amount_always_carries_its_currency(self):
        assert format_money("3.85", "GBP") == "3.85 GBP"
        assert format_money(8.4, "USD") == "8.40 USD"

    def test_currencies_are_never_added_together(self):
        # Exactly the figures that produced the bad screenshot: the Reports
        # page printed "12.25" for these two, a number true in no currency.
        label = format_money_map({"GBP": 3.85, "USD": 8.40})
        assert "3.85 GBP" in label
        assert "8.40 USD" in label
        assert "12.25" not in label

    def test_largest_first(self):
        assert format_money_map({"GBP": 3.85, "USD": 8.40}).startswith("8.40 USD")

    def test_one_currency_reads_as_one_plain_figure(self):
        assert format_money_map({"GBP": 3.85}) == "3.85 GBP"

    def test_handles_empty_and_unpriced(self):
        assert format_money_map({}) == "0.00"
        assert format_money("", "GBP") == ""
        assert format_money(None, None) == ""


def test_every_status_key_exists_in_every_locale():
    """A missing key makes display_status fall back to the humanised English
    code, which is the defect this was written to remove — silently, and only
    in the languages nobody checks."""
    keys = set(_STATUS_KEYS.values())
    missing = {}
    for path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        absent = sorted(keys - set(data))
        if absent:
            missing[path.stem] = absent
    assert not missing, f"status keys missing: {missing}"


@pytest.mark.parametrize(
    "func, raw",
    [
        (display_status, "in_transit"),
        (display_status, "purchased"),
        (display_status, "out_for_delivery"),
        (display_service, "FEDEX_GROUND"),
        (display_service, "RoyalMail2ndClass"),
        (display_carrier, "RoyalMailV3"),
    ],
)
def test_no_display_function_echoes_the_raw_value_back(func, raw):
    """The catch-all: every value here appeared verbatim on a published store
    listing. If any of these starts echoing the raw code again the regression
    is caught here rather than by someone reading a screenshot."""
    assert func(raw) != raw
