"""Customs declarations, shared by every path that can create a shipment.

This exists because it did not. The customs form, the currency mapping and the
declaration shape all lived inside CreateShipmentView, so the only code that
knew an international shipment needs `customs_info` was the single-shipment
screen. Batch shipments were built by a different function that never learned
it, and an international batch was therefore created happily and then failed at
purchase — after the batch was live — with the carrier's own phrasing:

    At least one item per package must be provided.

Nothing in the app said "customs" at that point, and the row had already passed
validation as complete. Anything that builds a shipment should build its
declaration from here, so a second caller cannot quietly miss it again.
"""

from typing import Optional

# The exemption citation for the ordinary case of an export below the value at
# which a real EEI filing is required. Some carriers reject a label outright
# with no eel_pfc at all, so it is always sent.
EEL_PFC_EXEMPTION = "NOEEI 30.37(a)"

CONTENTS_TYPES = ("merchandise", "documents", "gift", "sample", "returned_goods", "other")
RESTRICTION_TYPES = ("none", "other", "quarantine", "sanitary_phytosanitary_inspection")
NON_DELIVERY_OPTIONS = ("return", "abandon")

# Currency for a customs declared value, by origin country. Deliberately small:
# it covers the countries this app is actually used from, and anything else
# falls back to USD (EasyPost's own default). A customs form states a value in a
# currency, and hard-coding USD misstated every non-US sender's declaration.
CURRENCY_BY_COUNTRY = {
    "GB": "GBP", "US": "USD", "CA": "CAD", "AU": "AUD", "NZ": "NZD",
    "CH": "CHF", "JP": "JPY", "CN": "CNY", "IN": "INR", "SG": "SGD",
    "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN", "MX": "MXN",
    "BR": "BRL", "ZA": "ZAR", "AE": "AED", "HK": "HKD", "KR": "KRW",
    # The euro area.
    **{c: "EUR" for c in (
        "AT", "BE", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT", "LV",
        "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES", "HR",
    )},
}


def currency_for(country: Optional[str]) -> str:
    return CURRENCY_BY_COUNTRY.get((country or "").upper(), "USD")


def is_international(from_country: Optional[str], to_country: Optional[str]) -> bool:
    """True when a shipment crosses a border and therefore needs a declaration.

    Unknown on either side returns False: the caller has nothing to compare, and
    guessing "international" would demand customs details for a domestic parcel.
    """
    if not from_country or not to_country:
        return False
    return from_country.strip().upper() != to_country.strip().upper()


def customs_item(
    *,
    description: str,
    quantity: int,
    value: float,
    weight_oz: float,
    origin_country: str,
    currency: str,
    hs_tariff_number: str = "",
) -> dict:
    """One line on the declaration.

    `weight_oz` is ounces regardless of the unit the parcel form is showing —
    a customs item weight is always ounces, and sending a kilogram figure raw
    understates the declaration by a factor of 28.
    """
    item = {
        "description": description,
        "quantity": quantity,
        "value": value,
        "weight": weight_oz,
        "origin_country": (origin_country or "").upper(),
        "currency": currency,
    }
    # Omitted entirely when blank rather than sent as null. An explicit null is
    # a stated "no tariff code" where absence simply means the field was not
    # supplied, and some carriers treat the two differently.
    if hs_tariff_number:
        item["hs_tariff_number"] = hs_tariff_number
    return item


def build_customs_info(
    items: list[dict],
    *,
    customs_signer: str,
    contents_type: str = "merchandise",
    restriction_type: str = "none",
    non_delivery_option: str = "return",
    contents_explanation: str = "",
    restriction_comments: str = "",
) -> dict:
    """Assemble a `customs_info` payload, or raise ValueError naming the gap.

    The error strings are stable identifiers rather than prose so callers can
    map them onto their own translated messages.
    """
    if not items:
        raise ValueError("no_customs_items")
    if contents_type == "other" and not contents_explanation:
        raise ValueError("missing_contents_explanation")
    if not customs_signer:
        raise ValueError("missing_signer_or_certify")
    if restriction_type != "none" and not restriction_comments:
        raise ValueError("missing_restriction_comments")

    customs_info = {
        "contents_type": contents_type,
        "restriction_type": restriction_type,
        "non_delivery_option": non_delivery_option,
        "customs_certify": True,
        "customs_signer": customs_signer,
        "customs_items": items,
        "eel_pfc": EEL_PFC_EXEMPTION,
    }
    if contents_type == "other":
        customs_info["contents_explanation"] = contents_explanation
    if restriction_type != "none":
        customs_info["restriction_comments"] = restriction_comments
    return customs_info
