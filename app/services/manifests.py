"""Create and manage carrier manifests (EasyPost scan forms).

A manifest groups purchased shipments for end-of-day closeout with the
carrier. Royal Mail requires one before collection; without it the driver
has no despatch note and labels sit in limbo.

All shipments in a single manifest must share the same carrier and origin
address — that constraint comes from the carrier, not from EasyPost.

For Royal Mail shipments the app generates its own manifest PDF with the
correct customer name and service descriptions, because EasyPost's version
shows "Easypost" as the customer and uses simplified service names.  The
barcode encoding the Intersoft order reference is preserved so Royal Mail
can scan it at collection.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import NamedTuple, Optional

from app.core.client import client_manager
from app.core.db import db_cursor

log = logging.getLogger(__name__)


class ManifestError(ValueError):
    """Raised when the request would fail at EasyPost."""


class ManifestResult(NamedTuple):
    scan_form: object
    local_pdf_path: Optional[str]


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
    local_form_path: Optional[str] = None


def _shipment_services(shipment_ids: list[str]) -> dict[str, str]:
    """Look up the EasyPost service name for each shipment from the local DB."""
    if not shipment_ids:
        return {}
    mode = client_manager.active_mode
    placeholders = ", ".join("?" for _ in shipment_ids)
    with db_cursor() as cur:
        cur.execute(
            f"SELECT id, service FROM shipments WHERE mode = ? AND id IN ({placeholders})",
            [mode, *shipment_ids],
        )
        return {row["id"]: row["service"] for row in cur.fetchall() if row["service"]}


def create_manifest(shipment_ids: list[str]) -> ManifestResult:
    """Create a scan form via EasyPost and generate a corrected local PDF.

    Returns a :class:`ManifestResult` containing the raw scan form and the
    path to the locally generated PDF (or ``None`` if a corrected version
    could not be produced — the caller should fall back to ``form_url``).
    """
    if not shipment_ids:
        raise ManifestError("Select at least one purchased shipment to manifest.")

    client = client_manager.get_client()
    scan_form = client.scan_form.create(shipments=[{"id": sid} for sid in shipment_ids])

    # Generate corrected PDF for Royal Mail shipments
    local_path = None
    try:
        from app.core.manifest_pdf import build_manifest_for_scan_form

        svc_map = _shipment_services(shipment_ids)
        is_royal_mail = any("RoyalMail" in s for s in svc_map.values())
        if is_royal_mail:
            path = build_manifest_for_scan_form(scan_form, svc_map)
            if path:
                local_path = str(path)
    except Exception:
        log.warning("Local manifest PDF generation failed; using EasyPost URL", exc_info=True)

    return ManifestResult(scan_form=scan_form, local_pdf_path=local_path)


def save_manifest_locally(
    scan_form,
    shipment_ids: list[str],
    local_form_path: Optional[str] = None,
) -> None:
    mode = client_manager.active_mode
    tracking_codes = getattr(scan_form, "tracking_codes", []) or []

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO scan_forms (
                id, mode, status, form_url, tracking_codes,
                shipment_count, message, local_form_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, form_url=excluded.form_url,
                local_form_path=excluded.local_form_path
            """,
            (
                scan_form.id,
                mode,
                getattr(scan_form, "status", None),
                getattr(scan_form, "form_url", None),
                json.dumps(tracking_codes),
                len(tracking_codes),
                getattr(scan_form, "message", None),
                local_form_path,
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

        # local_form_path may be absent in databases predating this migration
        try:
            lfp = row["local_form_path"]
        except (IndexError, KeyError):
            lfp = None

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
                local_form_path=lfp,
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
