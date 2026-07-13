"""Shipment creation, rate shopping, label purchase, and local history sync."""

from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor


def create_shipment(
    *,
    to_address_id: str,
    from_address_id: str,
    length: float,
    width: float,
    height: float,
    weight: float,
    reference: str = "",
):
    """Create a shipment and get back live carrier rates. References existing
    verified addresses by EasyPost id rather than re-submitting full address
    fields.
    """
    client = client_manager.get_client()
    return client.shipment.create(
        to_address={"id": to_address_id},
        from_address={"id": from_address_id},
        parcel={
            "length": length,
            "width": width,
            "height": height,
            "weight": weight,
        },
        reference=reference or None,
    )


def buy_shipment(shipment_id: str, rate_id: str):
    client = client_manager.get_client()
    return client.shipment.buy(shipment_id, rate={"id": rate_id})


def regenerate_label(shipment_id: str, file_format: str = "PDF"):
    client = client_manager.get_client()
    return client.shipment.label(shipment_id, file_format=file_format)


def retrieve_shipment(shipment_id: str):
    client = client_manager.get_client()
    return client.shipment.retrieve(shipment_id)


def refund_shipment(shipment_id: str):
    """Requests a refund for a purchased-but-unused label. EasyPost sets
    refund_status to 'submitted' immediately; the carrier confirms
    'refunded' or 'rejected' asynchronously, so callers should re-check via
    retrieve_shipment/refresh_refund_status later.
    """
    client = client_manager.get_client()
    return client.shipment.refund(shipment_id)


def refresh_refund_status(shipment_id: str) -> Optional[str]:
    shipment = retrieve_shipment(shipment_id)
    status = getattr(shipment, "refund_status", None)
    update_refund_status(shipment_id, status)
    return status


def update_refund_status(shipment_id: str, refund_status: Optional[str]) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE shipments SET refund_status = ? WHERE id = ?",
            (refund_status, shipment_id),
        )


def _address_summary(address) -> str:
    if address is None:
        return ""
    parts = [
        getattr(address, "name", None) or getattr(address, "company", None),
        getattr(address, "city", None),
        getattr(address, "state", None),
    ]
    return ", ".join(p for p in parts if p)


def save_shipment_locally(shipment) -> None:
    mode = client_manager.active_mode
    selected_rate = getattr(shipment, "selected_rate", None)
    postage_label = getattr(shipment, "postage_label", None)
    insurance = getattr(shipment, "insurance", None)

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO shipments (
                id, mode, status, to_address, from_address, carrier, service,
                rate_amount, rate_currency, tracking_code, label_url,
                insured_amount, refund_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, carrier=excluded.carrier,
                service=excluded.service, rate_amount=excluded.rate_amount,
                rate_currency=excluded.rate_currency,
                tracking_code=excluded.tracking_code,
                label_url=excluded.label_url,
                insured_amount=excluded.insured_amount,
                refund_status=excluded.refund_status
            """,
            (
                shipment.id,
                mode,
                getattr(shipment, "status", None),
                _address_summary(getattr(shipment, "to_address", None)),
                _address_summary(getattr(shipment, "from_address", None)),
                getattr(selected_rate, "carrier", None) if selected_rate else None,
                getattr(selected_rate, "service", None) if selected_rate else None,
                getattr(selected_rate, "rate", None) if selected_rate else None,
                getattr(selected_rate, "currency", None) if selected_rate else None,
                getattr(shipment, "tracking_code", None),
                getattr(postage_label, "label_url", None) if postage_label else None,
                str(insurance) if insurance else None,
                getattr(shipment, "refund_status", None),
            ),
        )


@dataclass
class ShipmentRecord:
    id: str
    mode: str
    status: Optional[str]
    to_address: Optional[str]
    from_address: Optional[str]
    carrier: Optional[str]
    service: Optional[str]
    rate_amount: Optional[str]
    rate_currency: Optional[str]
    tracking_code: Optional[str]
    label_url: Optional[str]
    insured_amount: Optional[str]
    refund_status: Optional[str]


_SHIPMENT_FIELDS = [f for f in ShipmentRecord.__dataclass_fields__]


def list_shipments() -> list[ShipmentRecord]:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM shipments WHERE mode = ? ORDER BY created_at DESC",
            (mode,),
        )
        rows = cur.fetchall()
    return [ShipmentRecord(**{k: row[k] for k in _SHIPMENT_FIELDS}) for row in rows]
