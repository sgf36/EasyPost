"""Schedule, buy, and cancel carrier pickups for purchased shipments."""

from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor


def create_pickup(
    *,
    address_id: str,
    shipment_ids: list[str],
    min_datetime: str,
    max_datetime: str,
    instructions: str = "",
    reference: str = "",
):
    """Creates a pickup request (with quoted rates) for one or more already
    -purchased shipments at the given address and time window. Datetimes
    must be ISO-8601 strings, e.g. '2026-07-14T09:00:00-04:00'.
    """
    client = client_manager.get_client()
    return client.pickup.create(
        address={"id": address_id},
        shipments=[{"id": sid} for sid in shipment_ids],
        min_datetime=min_datetime,
        max_datetime=max_datetime,
        instructions=instructions or None,
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
