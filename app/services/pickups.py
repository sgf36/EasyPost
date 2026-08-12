"""Schedule, buy, and cancel carrier pickups for purchased shipments.

Three things about this endpoint, all verified against the live API, all of
which the previous implementation got wrong:

* A pickup takes **one** shipment under the singular key ``shipment``, not a
  ``shipments`` array. Sending the array is rejected outright with "Invalid
  request, a batch with shipments or a shipment is required to create a
  Pickup" — meaning pickup scheduling could never have succeeded. To collect
  several shipments at once, put them in a batch and pass ``batch``.
* ``instructions`` is **required**. Omitting it (as sending ``None`` does)
  fails with "pickup.instructions: instructions field is required".
* Datetimes want a UTC offset. A naive local timestamp leaves the carrier to
  guess the timezone of a collection window.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor

# EasyPost rejects a pickup with no instructions, so something has to be sent.
DEFAULT_INSTRUCTIONS = "Parcel ready for collection."


class PickupRequestError(ValueError):
    """Raised for a request EasyPost would reject, before it is sent."""


def ensure_offset(value: str) -> str:
    """Return an ISO-8601 datetime carrying a UTC offset.

    A collection window without one is ambiguous: the carrier has no way to
    know whether 10:00 means ten o'clock where the parcel is or somewhere else.
    A naive value is interpreted in the machine's own timezone, which is the
    only defensible reading of a time the user typed locally.
    """
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # Not something this can reason about — send it on and let EasyPost
        # judge it rather than mangling it.
        return text
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat()


def create_pickup(
    *,
    address_id: str,
    shipment_ids: list[str],
    min_datetime: str,
    max_datetime: str,
    instructions: str = "",
    reference: str = "",
):
    """Create a pickup request (with quoted rates) for an already-purchased
    shipment, at the given address and time window.

    ``shipment_ids`` stays a list for the callers that already pass one, but
    only a single shipment can be collected per pickup — see the module note.
    """
    if not shipment_ids:
        raise PickupRequestError("Choose a shipment for the carrier to collect.")
    if len(shipment_ids) > 1:
        raise PickupRequestError(
            "EasyPost collects one shipment per pickup request. "
            "Schedule a separate pickup for each, or create a batch first."
        )

    client = client_manager.get_client()
    return client.pickup.create(
        address={"id": address_id},
        shipment={"id": shipment_ids[0]},
        min_datetime=ensure_offset(min_datetime),
        max_datetime=ensure_offset(max_datetime),
        instructions=instructions.strip() or DEFAULT_INSTRUCTIONS,
        reference=reference or None,
    )


def buy_pickup(pickup_id: str, carrier: str, service: str):
    client = client_manager.get_client()
    return client.pickup.buy(pickup_id, carrier=carrier, service=service)


def cancel_pickup(pickup_id: str):
    client = client_manager.get_client()
    return client.pickup.cancel(pickup_id)


def save_pickup_locally(pickup, shipment_ids: list[str]) -> None:
    mode = client_manager.active_mode
    address = getattr(pickup, "address", None)
    address_summary = getattr(address, "city", "") if address else ""

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO pickups (
                id, mode, status, address, min_datetime, max_datetime, shipment_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status
            """,
            (
                pickup.id,
                mode,
                getattr(pickup, "status", None),
                address_summary,
                getattr(pickup, "min_datetime", None),
                getattr(pickup, "max_datetime", None),
                ",".join(shipment_ids),
            ),
        )


@dataclass
class PickupRecord:
    id: str
    mode: str
    status: Optional[str]
    address: Optional[str]
    min_datetime: Optional[str]
    max_datetime: Optional[str]
    shipment_ids: Optional[str]


_PICKUP_FIELDS = [f for f in PickupRecord.__dataclass_fields__]


def list_pickups() -> list[PickupRecord]:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM pickups WHERE mode = ? ORDER BY created_at DESC", (mode,)
        )
        rows = cur.fetchall()
    return [PickupRecord(**{k: row[k] for k in _PICKUP_FIELDS}) for row in rows]
