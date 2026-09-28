"""Royal Mail Click & Drop — service layer.

Wraps :mod:`app.core.click_drop_client` and produces objects the UI can
render alongside EasyPost rates without changes.  OBA accounts only:
labels are fetched via the API and rates show as account-billed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import APP_DATA_DIR, ensure_app_data_dir
from app.core import click_drop_client as api
from app.core.click_drop_client import ClickDropError
from app.core.client import client_manager
from app.core.credential_store import load_credentials
from app.core.db import db_cursor


# ── Normalised rate (duck-types EasyPost's rate object for the UI) ────

@dataclass
class ClickDropRate:
    id: str
    carrier: str
    service: str
    rate: str
    currency: str
    delivery_days: Optional[int]
    service_code: str
    package_format: str


# ── Normalised order result ───────────────────────────────────────────

@dataclass
class ClickDropOrder:
    order_identifier: int
    tracking_number: Optional[str]
    label_pdf: Optional[bytes]
    label_pdf_path: Optional[str]
    service_code: str
    service_name: str


# ── Public helpers ────────────────────────────────────────────────────

def click_drop_configured() -> bool:
    """True when the user has stored a Click & Drop API key."""
    return bool(load_credentials().click_drop_api_key)


def _api_key() -> str:
    key = load_credentials().click_drop_api_key
    if not key:
        raise ClickDropError("No Click & Drop API key configured.")
    return key


def test_connection() -> dict:
    """Validate the stored key by calling GET /version."""
    return api.get_version(_api_key())


# ── Service / rate fetching ───────────────────────────────────────────

_CARRIER_CODE = "ClickDrop"

_FORMAT_MAP = {
    "Letter": "letter",
    "LargeLetter": "largeLetter",
    "SmallParcel": "smallParcel",
    "MediumParcel": "mediumParcel",
    "Parcel": "parcel",
}


def _to_cd_format(weight_oz: float) -> str:
    """Best-guess Click & Drop packageFormatIdentifier from weight alone.

    Click & Drop filters services by format; without dimensions the best
    the app can do is a weight heuristic.  The caller should refine this
    when actual dimensions are available.
    """
    grams = weight_oz * 28.3495
    if grams <= 100:
        return "Letter"
    if grams <= 750:
        return "LargeLetter"
    if grams <= 2000:
        return "SmallParcel"
    return "Parcel"


def fetch_services(
    *,
    country_code: str,
    postcode: str = "",
    weight_grams: int = 0,
    package_format: str = "",
) -> list[ClickDropRate]:
    """Fetch available Click & Drop services and normalise them as rates.

    Returns an empty list (never raises) when the key is not configured or
    the API fails — Click & Drop is additive, never blocks EasyPost.
    """
    key = load_credentials().click_drop_api_key
    if not key:
        return []

    try:
        params: dict = {"country_code": country_code}
        if postcode:
            params["postcode"] = postcode
        if weight_grams:
            params["weight_grams"] = weight_grams
        if package_format:
            params["package_format"] = package_format

        raw = api.get_services(key, **params)
    except Exception:
        return []

    rates: list[ClickDropRate] = []
    for carrier_group in raw:
        for svc in carrier_group.get("services", []):
            code = svc.get("serviceCode", "")
            name = svc.get("serviceName", code)
            rates.append(
                ClickDropRate(
                    id=f"cd_{code}",
                    carrier=_CARRIER_CODE,
                    service=name,
                    rate="0.00",
                    currency="GBP",
                    delivery_days=None,
                    service_code=code,
                    package_format="",
                )
            )
    return rates


# ── Order creation (buy) ──────────────────────────────────────────────

def _labels_dir() -> Path:
    d = APP_DATA_DIR / "click_drop_labels"
    d.mkdir(parents=True, exist_ok=True)
    return d


def buy_click_drop(
    *,
    service_code: str,
    recipient: dict,
    sender: dict | None = None,
    packages: list[dict],
    order_reference: str = "",
) -> ClickDropOrder:
    """Create a Click & Drop order with inline label (OBA)."""
    key = _api_key()

    item: dict = {
        "orderReference": order_reference or None,
        "recipient": {"address": recipient},
        "packages": packages,
        "orderDate": _iso_now(),
        "subtotal": 0,
        "shippingCostCharged": 0,
        "total": 0,
        "postageDetails": {"serviceCode": service_code},
        "label": {"includeLabelInResponse": True, "includeCN": True},
    }
    if sender:
        item["sender"] = sender

    resp = api.create_orders(key, items=[item])

    created = (resp or {}).get("createdOrders", [])
    if not created:
        failed = (resp or {}).get("failedOrders", [])
        if failed:
            errors = failed[0].get("errors", [])
            msg = "; ".join(
                e.get("message", str(e)) for e in errors
            ) if errors else str(failed[0])
            raise ClickDropError(f"Click & Drop order failed: {msg}")
        raise ClickDropError("Click & Drop returned no order.")

    order = created[0]
    order_id = order["orderIdentifier"]
    tracking = order.get("trackingNumber")
    label_b64 = order.get("label")
    label_pdf = api.decode_label_from_response(label_b64)

    label_path = None
    if label_pdf:
        ensure_app_data_dir()
        label_path = str(_labels_dir() / f"cd_{order_id}.pdf")
        Path(label_path).write_bytes(label_pdf)

    svc_name = service_code
    return ClickDropOrder(
        order_identifier=order_id,
        tracking_number=tracking,
        label_pdf=label_pdf,
        label_pdf_path=label_path,
        service_code=service_code,
        service_name=svc_name,
    )


# ── Local persistence ─────────────────────────────────────────────────

def save_click_drop_locally(
    order: ClickDropOrder,
    mode: str | None = None,
    *,
    to_address: str = "",
    from_address: str = "",
) -> None:
    mode = mode or client_manager.active_mode
    shipment_id = f"cd_{order.order_identifier}"

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO click_drop_orders (
                order_identifier, mode, order_reference, service_code,
                tracking_number, label_pdf_path, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_identifier) DO UPDATE SET
                tracking_number=excluded.tracking_number,
                label_pdf_path=excluded.label_pdf_path,
                status=excluded.status
            """,
            (
                order.order_identifier,
                mode,
                "",
                order.service_code,
                order.tracking_number,
                order.label_pdf_path,
                "purchased",
            ),
        )
        cur.execute(
            """
            INSERT INTO shipments (
                id, mode, status, to_address, from_address, carrier, service,
                rate_amount, rate_currency, tracking_code, label_url,
                insured_amount, refund_status, provider
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, carrier=excluded.carrier,
                service=excluded.service, tracking_code=excluded.tracking_code,
                label_url=excluded.label_url, provider=excluded.provider
            """,
            (
                shipment_id,
                mode,
                "purchased",
                to_address,
                from_address,
                _CARRIER_CODE,
                order.service_name,
                "0.00",
                "GBP",
                order.tracking_number,
                order.label_pdf_path,
                None,
                None,
                "click_drop",
            ),
        )


# ── Utilities ─────────────────────────────────────────────────────────

def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
