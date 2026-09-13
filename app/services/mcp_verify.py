"""Build approval summaries from EasyPost, never from the agent's arguments.

This is the module that makes the approval dialog trustworthy. An agent asking
to buy something supplies only opaque identifiers — a shipment id and a rate
id. Everything a human is then shown is fetched back from EasyPost using those
ids alone.

The distinction matters. If the dialog echoed the agent's own description of
the purchase, a prompt-injected agent could describe a $400 international
express label as "USPS Ground, $7.20" and a person clicking quickly would
approve exactly what they were shown. Re-fetching means the displayed carrier,
service, price and both addresses are EasyPost's account of the purchase, so
the worst an agent can do is pick a *real* rate the user then declines.

Untrusted strings that end up on screen (names, street lines, descriptions)
are truncated and stripped of control characters here, so a recipient name
cannot smuggle newlines and fake extra dialog lines.
"""

from __future__ import annotations

import re
from typing import Optional

from app.core.client import client_manager
from app.services.rates import is_account_billed, is_placeholder_rate, rate_amount

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _refuse_placeholder(rate, rate_id: str) -> None:
    """A catalogue placeholder cannot be bought, and its 0.01 would sail under
    every ceiling, so it is never queued for a person to approve."""
    if is_placeholder_rate(rate):
        raise ValueError(
            f"Rate {rate_id} is a catalogue placeholder priced at {getattr(rate, 'rate', '?')}, "
            "not a real quote, and cannot be bought. Choose another rate."
        )


def clean(value, limit: int = 120) -> str:
    """Neutralise a value that came from outside before displaying it.

    Anything reaching the approval dialog may have been written by whoever
    addressed the parcel. Collapsing control characters stops a crafted value
    from forging extra lines in the dialog.
    """
    text = "" if value is None else str(value)
    text = _CONTROL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _address_line(address) -> str:
    if address is None:
        return "—"
    bits = [
        clean(getattr(address, "name", None) or getattr(address, "company", None), 60),
        clean(getattr(address, "street1", None), 60),
        clean(getattr(address, "city", None), 40),
        clean(getattr(address, "state", None), 20),
        clean(getattr(address, "zip", None), 20),
        clean(getattr(address, "country", None), 10),
    ]
    return ", ".join(b for b in bits if b) or "—"


def verify_shipment_purchase(shipment_id: str, rate_id: str) -> tuple[dict, Optional[float], Optional[str]]:
    """Re-fetch the shipment and confirm the rate genuinely belongs to it.

    Returns (summary, amount, currency). Raises ValueError if the rate is not
    one of the rates EasyPost has on that shipment — which also blocks an
    agent from pairing a cheap-looking rate id with a different shipment.
    """
    client = client_manager.get_client()
    shipment = client.shipment.retrieve(shipment_id)

    rates = getattr(shipment, "rates", None) or []
    match = next((r for r in rates if getattr(r, "id", None) == rate_id), None)
    if match is None:
        raise ValueError(
            f"Rate {rate_id} is not one of the rates on shipment {shipment_id}. "
            "Refusing to buy a rate that does not belong to this shipment."
        )

    _refuse_placeholder(match, rate_id)
    amount = rate_amount(match)

    summary = {
        "kind": "shipment",
        "shipment_id": clean(shipment_id, 40),
        "carrier": clean(getattr(match, "carrier", None), 40),
        "service": clean(getattr(match, "service", None), 60),
        "price": clean(getattr(match, "rate", None), 20),
        "currency": clean(getattr(match, "currency", None), 10),
        "delivery_days": clean(getattr(match, "delivery_days", None), 10),
        # Decided here, from the rate EasyPost returned, so the ceilings and
        # the approval card never have to reinterpret a bare 0.01.
        "account_billed": is_account_billed(match),
        "to": _address_line(getattr(shipment, "to_address", None)),
        "from": _address_line(getattr(shipment, "from_address", None)),
        "mode": client_manager.active_mode,
    }
    return summary, amount, summary["currency"] or None


def verify_refund(shipment_id: str) -> tuple[dict, Optional[float], Optional[str]]:
    """A refund does not spend, but it does destroy a paid-for label."""
    client = client_manager.get_client()
    shipment = client.shipment.retrieve(shipment_id)
    selected = getattr(shipment, "selected_rate", None)
    summary = {
        "kind": "refund",
        "shipment_id": clean(shipment_id, 40),
        "carrier": clean(getattr(selected, "carrier", None), 40) if selected else "—",
        "service": clean(getattr(selected, "service", None), 60) if selected else "—",
        "price": clean(getattr(selected, "rate", None), 20) if selected else "—",
        "tracking_code": clean(getattr(shipment, "tracking_code", None), 60),
        "to": _address_line(getattr(shipment, "to_address", None)),
        "mode": client_manager.active_mode,
    }
    # No amount: refunding does not draw down the spend ceiling.
    return summary, None, None


def verify_pickup_purchase(pickup_id: str, rate_id: str) -> tuple[dict, Optional[float], Optional[str]]:
    client = client_manager.get_client()
    pickup = client.pickup.retrieve(pickup_id)
    rates = getattr(pickup, "pickup_rates", None) or []
    match = next((r for r in rates if getattr(r, "id", None) == rate_id), None)
    if match is None:
        raise ValueError(
            f"Rate {rate_id} is not one of the pickup rates on {pickup_id}."
        )
    _refuse_placeholder(match, rate_id)
    amount = rate_amount(match)
    summary = {
        "kind": "pickup",
        "pickup_id": clean(pickup_id, 40),
        "carrier": clean(getattr(match, "carrier", None), 40),
        "service": clean(getattr(match, "service", None), 60),
        "price": clean(getattr(match, "rate", None), 20),
        "currency": clean(getattr(match, "currency", None), 10),
        "account_billed": is_account_billed(match),
        "mode": client_manager.active_mode,
    }
    return summary, amount, summary["currency"] or None
