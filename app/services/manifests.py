"""Create and manage carrier manifests (EasyPost scan forms).

A manifest groups purchased shipments for end-of-day closeout with the
carrier. Royal Mail requires one before collection; without it the driver
has no despatch note and labels sit in limbo.

All shipments in a single manifest must share the same carrier and origin
address — that constraint comes from the carrier, not from EasyPost.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor


class ManifestError(ValueError):
    """Raised when the request would fail at EasyPost."""


@dataclass
class ManifestRecord:
    id: str
    mode: str
    status: Optional[str]
    form_url: Optional[str]
    tracking_codes: list[str] = field(default_factory=list)
    shipment_count: int = 0
    message: Optional[str] = None
    created_at: Optional[str] = None


def create_manifest(shipment_ids: list[str]) -> object:
    """Call ``client.scan_form.create()`` for the given shipment IDs.

    Returns the raw EasyPost ScanForm object. The caller is responsible
    for calling :func:`save_manifest_locally` with the result.
    """
    if not shipment_ids:
        raise ManifestError("Select at least one purchased shipment to manifest.")

    client = client_manager.get_client()
    return client.scan_form.create(shipments=[{"id": sid} for sid in shipment_ids])


def save_manifest_locally(scan_form, shipment_ids: list[str]) -> None:
    mode = client_manager.active_mode
    tracking_codes = getattr(scan_form, "tracking_codes", []) or []

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO scan_forms (
                id, mode, status, form_url, tracking_codes,
                shipment_count, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, form_url=excluded.form_url
            """,
            (
                scan_form.id,
                mode,
                getattr(scan_form, "status", None),
                getattr(scan_form, "form_url", None),
                json.dumps(tracking_codes),
                len(tracking_codes),
                getattr(scan_form, "message", None),
            ),
        )
        for sid in shipment_ids:
            cur.execute(
                "UPDATE shipments SET scan_form_id = ? WHERE id = ?",
                (scan_form.id, sid),
            )


def list_manifests() -> list[ManifestRecord]:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM scan_forms WHERE mode = ? ORDER BY created_at DESC",
            (mode,),
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        codes_raw = row["tracking_codes"]
        try:
            codes = json.loads(codes_raw) if codes_raw else []
        except (json.JSONDecodeError, TypeError):
            codes = []
        results.append(
            ManifestRecord(
                id=row["id"],
                mode=row["mode"],
                status=row["status"],
                form_url=row["form_url"],
                tracking_codes=codes,
                shipment_count=row["shipment_count"] or 0,
                message=row["message"],
                created_at=row["created_at"],
            )
        )
    return results


def unmanifested_shipments():
    """Return purchased shipments not yet covered by a manifest."""
    from app.services.shipments import ShipmentRecord, _SHIPMENT_FIELDS

    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM shipments "
            "WHERE mode = ? AND scan_form_id IS NULL "
            "AND tracking_code IS NOT NULL "
            "ORDER BY created_at DESC",
            (mode,),
        )
        rows = cur.fetchall()
    return [ShipmentRecord(**{k: row[k] for k in _SHIPMENT_FIELDS}) for row in rows]
