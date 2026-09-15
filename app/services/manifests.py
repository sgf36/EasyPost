"""Royal Mail manifesting via EasyPost ScanForms.

Manifesting is mandatory for Royal Mail (v3): all shipments must be
manifested before pickup. A ScanForm groups purchased shipments sharing
the same origin address into a manifest document with a scannable barcode
the pickup driver scans at collection.

ScanForms are immutable once created. A shipment can belong to at most one
ScanForm, and return shipments cannot be manifested.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor

logger = logging.getLogger(__name__)


def create_scan_form(shipment_ids: list[str]):
    """Create a ScanForm for the given purchased shipments.

    All shipments must share the same origin address and carrier account.
    Returns the ScanForm object (status may be ``creating`` — poll with
    ``retrieve_scan_form`` until ``created`` or ``failed``).
    """
    if not shipment_ids:
        raise ValueError("At least one shipment is required.")
    client = client_manager.get_client()
    shipments = [{"id": sid} for sid in shipment_ids]
    return client.scan_form.create(shipments=shipments)


def retrieve_scan_form(scan_form_id: str):
    client = client_manager.get_client()
    return client.scan_form.retrieve(scan_form_id)


def list_scan_forms(
    *,
    page_size: int = 20,
    before_id: Optional[str] = None,
    after_id: Optional[str] = None,
) -> dict:
    client = client_manager.get_client()
    params: dict = {"page_size": page_size}
    if before_id:
        params["before_id"] = before_id
    if after_id:
        params["after_id"] = after_id
    return client.scan_form.all(**params)


def save_scan_form_locally(scan_form) -> None:
    mode = client_manager.active_mode
    address = getattr(scan_form, "address", None)
    address_str = None
    if address:
        parts = [
            getattr(address, "street1", ""),
            getattr(address, "city", ""),
            getattr(address, "state", ""),
            getattr(address, "zip", ""),
            getattr(address, "country", ""),
        ]
        address_str = ", ".join(p for p in parts if p)

    tracking_codes = getattr(scan_form, "tracking_codes", None) or []
    codes_str = ",".join(tracking_codes) if tracking_codes else None

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO scan_forms (id, mode, status, message, address,
                                    tracking_codes, form_url, batch_id,
                                    num_shipments, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, message=excluded.message,
                form_url=excluded.form_url,
                tracking_codes=excluded.tracking_codes
            """,
            (
                scan_form.id,
                mode,
                getattr(scan_form, "status", None),
                getattr(scan_form, "message", None),
                address_str,
                codes_str,
                getattr(scan_form, "form_url", None),
                getattr(scan_form, "batch_id", None),
                len(tracking_codes),
                getattr(scan_form, "created_at", None),
            ),
        )


@dataclass
class ScanFormRecord:
    id: str
    mode: str
    status: Optional[str]
    message: Optional[str]
    address: Optional[str]
    tracking_codes: Optional[str]
    form_url: Optional[str]
    batch_id: Optional[str]
    num_shipments: Optional[int]
    created_at: Optional[str]


_SCAN_FORM_FIELDS = list(ScanFormRecord.__dataclass_fields__)


def list_local_scan_forms() -> list[ScanFormRecord]:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM scan_forms WHERE mode = ? ORDER BY created_at DESC",
            (mode,),
        )
        rows = cur.fetchall()
    return [ScanFormRecord(**{k: row[k] for k in _SCAN_FORM_FIELDS}) for row in rows]


def manifestable_shipment_ids() -> list[dict]:
    """Shipments eligible for manifesting: purchased, with a tracking code,
    not yet in any scan form."""
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.tracking_code, s.carrier, s.service,
                   s.to_address, s.from_address, s.created_at
            FROM shipments s
            WHERE s.mode = ?
              AND s.tracking_code IS NOT NULL
              AND s.refund_status IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM scan_forms sf
                  WHERE sf.mode = ?
                    AND (',' || sf.tracking_codes || ',')
                        LIKE ('%,' || s.tracking_code || ',%')
              )
            ORDER BY s.created_at DESC
            """,
            (mode, mode),
        )
        return [dict(row) for row in cur.fetchall()]
