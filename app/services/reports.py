"""Local aggregate reporting over shipment history (current mode only).

Everything here is keyed by currency. A single total across currencies is not a
number: this module used to sum 3.85 GBP and 8.40 USD into "12.25", and that
figure reached both the Microsoft Store and App Store listings. There is no
exchange rate in this application and there should not be one — the honest
answer is two figures.
"""

from collections import Counter, defaultdict

from app.services.shipments import list_shipments

# What a shipment with no currency recorded is filed under. Older rows predate
# the column, and an empty string sorts and formats badly.
UNKNOWN_CURRENCY = ""


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def spend_by_carrier() -> dict[str, dict[str, float]]:
    """``{carrier: {currency: amount}}`` — never a bare per-carrier float."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for rec in list_shipments():
        if rec.rate_amount:
            currency = (rec.rate_currency or UNKNOWN_CURRENCY).strip()
            totals[rec.carrier or "Unknown"][currency] += _to_float(rec.rate_amount)
    return {carrier: dict(by_ccy) for carrier, by_ccy in totals.items()}


def total_spend_by_currency() -> dict[str, float]:
    """``{currency: amount}`` across every shipment in this mode."""
    totals: dict[str, float] = defaultdict(float)
    for rec in list_shipments():
        if rec.rate_amount:
            totals[(rec.rate_currency or UNKNOWN_CURRENCY).strip()] += _to_float(rec.rate_amount)
    return dict(totals)


def primary_currency() -> str:
    """The currency most of the spend is in.

    A bar chart needs one axis, and an axis needs one unit. Rather than plot
    incomparable bars side by side, the chart shows this currency and names it
    in its own title; anything else is reported separately.
    """
    totals = total_spend_by_currency()
    if not totals:
        return UNKNOWN_CURRENCY
    return max(totals.items(), key=lambda kv: (kv[1], kv[0]))[0]


def label_counts_by_status() -> dict:
    return dict(Counter(rec.status or "unknown" for rec in list_shipments()))


def refund_status_breakdown() -> dict:
    return dict(Counter(rec.refund_status or "none" for rec in list_shipments()))


def total_labels_purchased() -> int:
    return sum(1 for rec in list_shipments() if rec.tracking_code)
