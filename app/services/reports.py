"""Local aggregate reporting over shipment history (current mode only).

Everything here is keyed by currency. A single total across currencies is not a
number: this module used to sum 3.85 GBP and 8.40 USD into "12.25", and that
figure reached both the Microsoft Store and App Store listings. There is no
exchange rate in this application and there should not be one — the honest
answer is two figures.

Spend is what was paid and not given back. A label whose refund EasyPost reports
as ``refunded`` is not spend: counting it made three 5.00 labels, two of them
refunded, read as 15.00 spent for good. A refund that is only ``submitted``
still counts, because the carrier can reject it, and it is reported beside the
total as awaiting refund rather than being folded in or dropped silently.

Every function takes the records it reads. A page that shows several figures
reads the shipments once and passes them to each; reading them per figure made
Reports query every shipment six times on each refresh.
"""

from collections import Counter, defaultdict
from typing import Iterable, Optional

from app.services.shipments import ShipmentRecord, is_refund_pending, is_refunded, list_shipments

# What a shipment with no currency recorded is filed under. Older rows predate
# the column, and an empty string sorts and formats badly.
UNKNOWN_CURRENCY = ""


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _records(records: Optional[Iterable[ShipmentRecord]]) -> Iterable[ShipmentRecord]:
    return list_shipments() if records is None else records


def _currency(rec: ShipmentRecord) -> str:
    return (rec.rate_currency or UNKNOWN_CURRENCY).strip()


def _sum_by_currency(records: Iterable[ShipmentRecord]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for rec in records:
        if rec.rate_amount:
            totals[_currency(rec)] += _to_float(rec.rate_amount)
    return dict(totals)


def spend_by_carrier(records=None) -> dict[str, dict[str, float]]:
    """``{carrier: {currency: amount}}`` — never a bare per-carrier float.

    Refunded labels are left out here too, or the bars would add up to more
    than the total printed above them.
    """
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for rec in _records(records):
        if rec.rate_amount and not is_refunded(rec.refund_status):
            totals[rec.carrier or "Unknown"][_currency(rec)] += _to_float(rec.rate_amount)
    return {carrier: dict(by_ccy) for carrier, by_ccy in totals.items()}


def total_spend_by_currency(records=None) -> dict[str, float]:
    """``{currency: amount}`` across every shipment in this mode, less the
    labels whose refund the carrier has confirmed.

    A caller that has already read the shipments passes them in, so the
    Dashboard's figure is this function's figure rather than a second copy of
    the rule that could drift from Reports.
    """
    return _sum_by_currency(r for r in _records(records) if not is_refunded(r.refund_status))


def pending_refund_by_currency(records=None) -> dict[str, float]:
    """Postage on labels whose refund is submitted but not yet confirmed.

    Already inside total_spend_by_currency. Reported on its own so a person can
    see how much of that spend may still come back.
    """
    return _sum_by_currency(r for r in _records(records) if is_refund_pending(r.refund_status))


def refunded_by_currency(records=None) -> dict[str, float]:
    """Postage the carrier has refunded, which spend leaves out."""
    return _sum_by_currency(r for r in _records(records) if is_refunded(r.refund_status))


def primary_currency(records=None) -> str:
    """The currency most of the spend is in.

    A bar chart needs one axis, and an axis needs one unit. Rather than plot
    incomparable bars side by side, the chart shows this currency and names it
    in its own title; anything else is reported separately.
    """
    totals = total_spend_by_currency(records)
    if not totals:
        return UNKNOWN_CURRENCY
    return max(totals.items(), key=lambda kv: (kv[1], kv[0]))[0]


def label_counts_by_status(records=None) -> dict:
    return dict(Counter(rec.status or "unknown" for rec in _records(records)))


def refund_status_breakdown(records=None) -> dict:
    return dict(Counter(rec.refund_status or "none" for rec in _records(records)))


def total_labels_purchased(records=None) -> int:
    # A refunded label was still bought, so it stays in this count; only the
    # money figures leave it out.
    return sum(1 for rec in _records(records) if rec.tracking_code)
