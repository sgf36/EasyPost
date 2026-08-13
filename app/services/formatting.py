"""Turning API values into something a person should read.

Every function here existed already — as private classmethods on
``CreateShipmentView``. That is why the Create Shipment page rendered
"Royal Mail V3", "FedEx Ground" and "DHL Express" correctly while Tracking
showed ``in_transit``, History showed ``RoyalMailV3``, Pickups showed
``FEDEX_GROUND`` and the Reports chart labelled its bars with raw codes. The
fix for "Fed Ex" reaching a published screenshot (6070f4a) repaired the one
view that owned the helper and left every other view reading the raw field.

So the rule is: **no view formats an API value itself.** A code reaches the
screen through one of these functions or it does not reach the screen.

Money is here for the same reason. Spend used to be summed across currencies
and printed as one figure, so 3.85 GBP plus 8.40 USD read "12.25" — a number
that is not true in any currency. It reached both store listings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.i18n import tr
from app.services.carriers import carrier_display_name, carrier_is_known

# Split at camelCase and letter/digit boundaries: InternationalBusinessParcels
# -> International Business Parcels, RoyalMailV3 -> Royal Mail V3. The middle
# alternative is what keeps an acronym followed by a word apart —
# USPSPriority -> USPS Priority, rather than "USPSPriority" or "U S P S".
_CAMEL_SPLIT = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])"
)

# Title-casing an all-caps service name gets the brand wrong: FEDEX_GROUND
# becomes "Fedex Ground", sitting under a group header that correctly reads
# "FedEx". Restore the handful whose own capitalisation is not title case.
_BRAND_CASE = {
    "Fedex": "FedEx",
    "Usps": "USPS",
    "Ups": "UPS",
    "Dhl": "DHL",
    "Ontrac": "OnTrac",
    "Lasership": "LaserShip",
}

# Statuses EasyPost reports, as translatable keys. Carrier and service names
# are brand names and stay in their own spelling in every language; a status is
# ordinary prose and does not. "In transit" sitting in an otherwise Japanese
# window is the same defect as `in_transit`, one step less obvious.
_STATUS_KEYS = {
    "pre_transit": "status.pre_transit",
    "in_transit": "status.in_transit",
    "out_for_delivery": "status.out_for_delivery",
    "delivered": "status.delivered",
    "available_for_pickup": "status.available_for_pickup",
    "return_to_sender": "status.return_to_sender",
    "failure": "status.failure",
    "cancelled": "status.cancelled",
    "canceled": "status.cancelled",
    "error": "status.error",
    "purchased": "status.purchased",
    "refunded": "status.refunded",
    "submitted": "status.submitted",
    "rejected": "status.rejected",
    "scheduled": "status.scheduled",
    "none": "status.none",
    "unknown": "status.unknown",
    # EasyPost's documented `status_detail` vocabulary. A detail is the line
    # that says *why* — "Failed — address incorrect" — and it used to be the one
    # string in a fifty-language application that stayed in English, because it
    # arrives from the carrier rather than from us. Arriving from elsewhere is
    # not a reason to print it raw at a user.
    #
    # Six of them are the same words as a status above and share its key.
    # Anything outside this list still falls back to humanize_code, so an
    # unrecognised detail reads as words rather than as an identifier.
    "address_correction": "status.address_correction",
    "arrived_at_destination": "status.arrived_at_destination",
    "arrived_at_facility": "status.arrived_at_facility",
    "arrived_at_pickup_location": "status.arrived_at_pickup_location",
    "awaiting_information": "status.awaiting_information",
    "damaged": "status.damaged",
    "delayed": "status.delayed",
    "delivery_exception": "status.delivery_exception",
    "departed_facility": "status.departed_facility",
    "departed_origin_facility": "status.departed_origin_facility",
    "expired": "status.expired",
    "held": "status.held",
    "label_created": "status.label_created",
    "missorted": "status.missorted",
    "out_of_transit": "status.out_of_transit",
    "package_accepted": "status.package_accepted",
    "package_arrived": "status.package_arrived",
    "package_departed": "status.package_departed",
    "package_forwarded": "status.package_forwarded",
    "package_held": "status.package_held",
    "package_processed": "status.package_processed",
    "package_processing": "status.package_processing",
    "received_at_destination": "status.received_at_destination",
    "received_at_origin_facility": "status.received_at_origin_facility",
    "refused": "status.refused",
    "rescheduled": "status.rescheduled",
    "status_update": "status.status_update",
    "transit_exception": "status.transit_exception",
    "weather_delay": "status.weather_delay",
    "return": "status.return_to_sender",
}


def humanize_code(name: str) -> str:
    """Space out a run-together API code so it reads as words.

    ``InternationalBusinessParcelsTracked30kg`` becomes "International Business
    Parcels Tracked 30kg"; ``FEDEX_GROUND`` becomes "FedEx Ground". Names that
    already contain spaces are left alone, so this is safe to apply twice.
    """
    if not name or " " in name:
        return name
    if "_" in name:
        return " ".join(
            # Three letters counts: FEDEX_2_DAY was leaving "DAY" shouting.
            # Genuine three-letter acronyms come back through _BRAND_CASE.
            _BRAND_CASE.get(part.title(), part.title())
            if part.isupper() and len(part) > 2
            else part
            for part in name.split("_")
            if part
        )
    return _rejoin_version(_CAMEL_SPLIT.sub(" ", name))


def _rejoin_version(text: str) -> str:
    """Put a version marker back together: "Royal Mail V 3" -> "Royal Mail V3".

    The letter/digit split is right for "Tracked 30kg" and wrong for "V3",
    and the two are indistinguishable before the split. A lone letter left
    stranded in front of a number is always a version, never a word.
    """
    parts = text.split(" ")
    out: list[str] = []
    for part in parts:
        if out and len(out[-1]) == 1 and out[-1].isalpha() and part[:1].isdigit():
            out[-1] += part
        else:
            out.append(part)
    return " ".join(out)


def display_carrier(carrier: str, *, blank: str = "") -> str:
    """The name to show for a carrier code.

    Ask the catalogue whether it knows the carrier rather than inferring it
    from the returned name. Some carriers' display name IS their code, so
    comparing the two calls them unrecognised and camel-splits them: FedEx
    reached a published store screenshot as "Fed Ex" that way.
    """
    if not carrier:
        return blank
    if carrier_is_known(carrier):
        return carrier_display_name(carrier)
    return humanize_code(carrier)


def display_service(service: str, *, blank: str = "") -> str:
    """The name to show for a carrier service code."""
    return humanize_code(service) if service else blank


def display_status(status: str | None, detail: str | None = None, *, blank: str = "") -> str:
    """A status line a person can read, with the noise dropped.

    ``unknown`` is what EasyPost sends whenever a carrier has not elaborated,
    which is most of the time. Printed verbatim beside a real status it reads as
    the app not knowing what is happening, so a detail that says nothing — empty,
    "unknown", or a restatement of the status — is left out entirely.
    """
    if not status:
        return blank
    text = _translate_status(status, sentence=True)
    if detail:
        cleaned = detail.strip()
        if (
            cleaned
            and cleaned.casefold() not in {"unknown", (status or "").casefold()}
            and cleaned.casefold() != text.casefold()
        ):
            # The detail trails the status after a dash, so it stays lower
            # case — "Failed — address incorrect" reads as one sentence,
            # "Failed — Address Incorrect" as two competing headings.
            text = f"{text} — {_translate_status(cleaned, sentence=False)}"
    return text


def _translate_status(value: str, *, sentence: bool) -> str:
    key = _STATUS_KEYS.get(value.strip().casefold())
    if key:
        translated = tr(key)
        # tr() echoes the key back when a catalogue is missing it; falling
        # through to the humanised code beats printing "status.in_transit".
        if translated != key:
            return translated
    # An unmapped status is still a status: humanise it rather than printing
    # some_new_state, and capitalise it if it is leading the line.
    text = humanize_code(value.strip()) or value
    return text[:1].upper() + text[1:] if sentence and text else text


def format_money(amount, currency: str | None) -> str:
    """One amount with its currency. Never an amount on its own."""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return ""
    return f"{value:.2f} {currency}".strip() if currency else f"{value:.2f}"


def format_money_map(totals: Mapping[str, float]) -> str:
    """Several currencies side by side, largest first — never added together.

    Adding them is the bug this exists to prevent: there is no exchange rate in
    this application, and inventing one silently is worse than showing two
    figures. A single currency still reads as one plain figure.
    """
    real = {c: v for c, v in (totals or {}).items() if v}
    if not real:
        return format_money(0, next(iter(totals or {}), None) or None)
    return " + ".join(
        format_money(v, c or None)
        for c, v in sorted(real.items(), key=lambda kv: (-kv[1], kv[0]))
    )
