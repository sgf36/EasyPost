"""Local aggregate reporting over shipment history (current mode only)."""

from collections import Counter, defaultdict

from app.services.shipments import list_shipments


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def spend_by_carrier() -> dict:
    totals: dict[str, float] = defaultdict(float)
    for rec in list_shipments():
        if rec.rate_amount:
            totals[rec.carrier or "Unknown"] += _to_float(rec.rate_amount)
    return dict(totals)


def label_counts_by_status() -> dict:
    return dict(Counter(rec.status or "unknown" for rec in list_shipments()))


def refund_status_breakdown() -> dict:
    return dict(Counter(rec.refund_status or "none" for rec in list_shipments()))


def total_spend() -> float:
    return sum(_to_float(rec.rate_amount) for rec in list_shipments() if rec.rate_amount)


def total_labels_purchased() -> int:
    return sum(1 for rec in list_shipments() if rec.tracking_code)
