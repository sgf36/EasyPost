"""Royal Mail Click & Drop — service layer.

Wraps :mod:`app.core.click_drop_client` and produces objects the UI can
render alongside EasyPost rates without changes.  OBA accounts only:
labels are fetched via the API and rates show as account-billed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from app.config import APP_DATA_DIR, PAIR_PROXY_URL, ensure_app_data_dir
from app.core import click_drop_client as api
from app.core.click_drop_client import ClickDropError
from app.core.client import client_manager
from app.core.countries import to_alpha3
from app.core.credential_store import load_credentials
from app.core.db import db_cursor
from app.core.settings import load_settings

logger = logging.getLogger(__name__)


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
    "LargeParcel": "largeParcel",
    "Parcel": "parcel",
    "Documents": "documents",
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
        params: dict = {"country_code": to_alpha3(country_code)}
        if postcode:
            params["postcode"] = postcode
        if weight_grams:
            params["weight_grams"] = weight_grams
        if package_format:
            params["package_format"] = package_format

        raw = api.get_services(key, **params)
    except Exception:
        logger.exception("Click & Drop services fetch failed")
        return []

    return _parse_services(raw, weight_grams)


def _parse_services(raw, weight_grams: int = 0) -> list[ClickDropRate]:
    """Normalise the Click & Drop /services response into ClickDropRate objects.

    The API returns nested carrier groups, each with a ``services`` list.
    """
    if not raw:
        return []

    services: list[dict] = []
    if isinstance(raw, dict):
        services = raw.get("services", [])
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "serviceCode" in item:
                services.append(item)
            elif "services" in item:
                services.extend(item.get("services", []))
    else:
        logger.warning("Unexpected Click & Drop services response type: %s", type(raw))
        return []

    fallback_fmt = _to_cd_format(weight_grams / 28.3495) if weight_grams else ""
    rates: list[ClickDropRate] = []
    seen: set[str] = set()
    for svc in services:
        code = svc.get("serviceCode", "")
        if not code or code in seen:
            continue
        seen.add(code)
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
                package_format=_FORMAT_MAP.get(fallback_fmt, fallback_fmt),
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
    consequential_loss: int = 0,
) -> ClickDropOrder:
    """Create a Click & Drop order with inline label (OBA)."""
    key = _api_key()

    if "countryCode" in recipient:
        recipient = {**recipient, "countryCode": to_alpha3(recipient["countryCode"])}

    postage: dict = {"serviceCode": service_code}
    if consequential_loss > 0:
        postage["consequentialLoss"] = consequential_loss

    item: dict = {
        "orderReference": order_reference or None,
        "recipient": {"address": recipient},
        "packages": packages,
        "orderDate": _iso_now(),
        "subtotal": 0,
        "shippingCostCharged": 0,
        "total": 0,
        "postageDetails": postage,
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
    result = ClickDropOrder(
        order_identifier=order_id,
        tracking_number=tracking,
        label_pdf=label_pdf,
        label_pdf_path=label_path,
        service_code=service_code,
        service_name=svc_name,
    )
    _sync_orders_to_proxy([{
        "order_identifier": order_id,
        "tracking_number": tracking,
        "service_name": svc_name,
        "order_reference": order_reference or "",
        "status": "purchased",
        "created_at": _iso_now(),
    }])
    return result


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


# ── Bulk order creation (batch) ───────────────────────────────────────

@dataclass
class BulkClickDropResult:
    created: list[ClickDropOrder]
    failed: list[dict]


def bulk_buy_click_drop(
    *,
    rows: list,
    service_code: str,
    from_address: dict | None = None,
) -> BulkClickDropResult:
    """Create Click & Drop orders in bulk from batch-imported rows.

    Click & Drop accepts up to 2000 items per call.  Each ``row`` is a
    BatchRow-like object with a ``.fields`` dict carrying the CSV columns.
    """
    key = _api_key()
    order_date = _iso_now()

    items: list[dict] = []
    for row in rows:
        f = row.fields
        weight_oz = float(f.get("weight", 0) or 0)
        weight_g = int(weight_oz * 28.3495) if weight_oz else 0
        fmt = _to_cd_format(weight_oz)
        country_raw = (f.get("to_country", "") or "GB").upper()
        recipient = {
            "fullName": (f.get("to_name", "") or "").strip(),
            "companyName": (f.get("to_company", "") or "").strip(),
            "addressLine1": (f.get("to_street1", "") or "").strip(),
            "addressLine2": (f.get("to_street2", "") or "").strip(),
            "addressLine3": "",
            "city": (f.get("to_city", "") or "").strip(),
            "county": (f.get("to_state", "") or "").strip(),
            "postcode": (f.get("to_zip", "") or "").strip(),
            "countryCode": to_alpha3(country_raw),
            "phoneNumber": (f.get("to_phone", "") or "").strip(),
            "emailAddress": (f.get("to_email", "") or "").strip(),
        }
        pkg: dict = {"weightInGrams": weight_g, "packageFormatIdentifier": fmt}
        length = float(f.get("length", 0) or 0)
        width = float(f.get("width", 0) or 0)
        height = float(f.get("height", 0) or 0)
        if length and width and height:
            pkg["heightInMms"] = round(height * 25.4)
            pkg["widthInMms"] = round(width * 25.4)
            pkg["depthInMms"] = round(length * 25.4)

        item: dict = {
            "orderReference": f.get("reference", "") or None,
            "recipient": {"address": recipient},
            "packages": [pkg],
            "orderDate": order_date,
            "subtotal": 0,
            "shippingCostCharged": 0,
            "total": 0,
            "postageDetails": {"serviceCode": service_code},
            "label": {"includeLabelInResponse": True, "includeCN": True},
        }
        if from_address:
            item["sender"] = from_address
        items.append(item)

    resp = api.create_orders(key, items=items)

    created_orders: list[ClickDropOrder] = []
    for order in (resp or {}).get("createdOrders", []):
        order_id = order["orderIdentifier"]
        tracking = order.get("trackingNumber")
        label_b64 = order.get("label")
        label_pdf = api.decode_label_from_response(label_b64)
        label_path = None
        if label_pdf:
            ensure_app_data_dir()
            label_path = str(_labels_dir() / f"cd_{order_id}.pdf")
            Path(label_path).write_bytes(label_pdf)
        created_orders.append(ClickDropOrder(
            order_identifier=order_id,
            tracking_number=tracking,
            label_pdf=label_pdf,
            label_pdf_path=label_path,
            service_code=service_code,
            service_name=service_code,
        ))

    failed_orders = (resp or {}).get("failedOrders", [])
    if created_orders:
        _sync_orders_to_proxy([{
            "order_identifier": o.order_identifier,
            "tracking_number": o.tracking_number,
            "service_name": o.service_name,
            "order_reference": "",
            "status": "purchased",
            "created_at": order_date,
        } for o in created_orders])
    return BulkClickDropResult(created=created_orders, failed=failed_orders)


# ── Order void / cancel ──────────────────────────────────────────

def void_click_drop_order(order_identifier: int) -> None:
    """Void (cancel) a Click & Drop order.  Deletes the label."""
    key = _api_key()
    api.delete_order(key, order_identifier)

    with db_cursor() as cur:
        cur.execute(
            "UPDATE click_drop_orders SET status = ? WHERE order_identifier = ?",
            ("voided", order_identifier),
        )
        cur.execute(
            "UPDATE shipments SET refund_status = ? WHERE id = ?",
            ("voided", f"cd_{order_identifier}"),
        )
    _sync_orders_to_proxy([{
        "order_identifier": order_identifier,
        "tracking_number": None,
        "service_name": "",
        "order_reference": "",
        "status": "voided",
        "created_at": _iso_now(),
    }])


# ── CD-specific manifest ────────────────────────────────────────

@dataclass
class ClickDropManifest:
    manifest_id: int | None
    document_pdf: bytes | None
    pdf_path: str | None


def create_click_drop_manifest() -> ClickDropManifest:
    """Manifest all eligible Click & Drop orders.

    Returns 201 with a PDF or 202 if the manifest is still being
    generated (call ``get_click_drop_manifest`` to retrieve it later).
    """
    key = _api_key()
    resp = api.create_manifest(key)

    manifest_id = (resp or {}).get("manifestId")
    doc_b64 = (resp or {}).get("documentPdf")
    pdf_bytes = None
    pdf_path = None
    if doc_b64:
        import base64
        try:
            pdf_bytes = base64.b64decode(doc_b64)
        except Exception:
            pass
    if pdf_bytes:
        ensure_app_data_dir()
        pdf_path = str(_labels_dir() / f"manifest_{manifest_id or 'latest'}.pdf")
        Path(pdf_path).write_bytes(pdf_bytes)

    return ClickDropManifest(
        manifest_id=manifest_id,
        document_pdf=pdf_bytes,
        pdf_path=pdf_path,
    )


def get_click_drop_manifest(manifest_id: int) -> ClickDropManifest:
    """Retrieve a previously created manifest by ID."""
    key = _api_key()
    resp = api.get_manifest(key, manifest_id)

    doc_b64 = (resp or {}).get("documentPdf")
    pdf_bytes = None
    pdf_path = None
    if doc_b64:
        import base64
        try:
            pdf_bytes = base64.b64decode(doc_b64)
        except Exception:
            pass
    if pdf_bytes:
        ensure_app_data_dir()
        pdf_path = str(_labels_dir() / f"manifest_{manifest_id}.pdf")
        Path(pdf_path).write_bytes(pdf_bytes)

    return ClickDropManifest(
        manifest_id=manifest_id,
        document_pdf=pdf_bytes,
        pdf_path=pdf_path,
    )


# ── Utilities ─────────────────────────────────────────────────────────

def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sync_orders_to_proxy(orders: list[dict]) -> None:
    """Push Click & Drop order summaries to the mobile proxy (fire-and-forget).

    Silently swallows failures — the mobile companion seeing orders is a
    convenience, not a gate on the desktop workflow.
    """
    if not orders:
        return
    key = load_credentials().production_key
    license_key = (load_settings().license_key or "").strip()
    if not key or not license_key:
        return
    try:
        requests.post(
            f"{PAIR_PROXY_URL}/clickdrop/sync",
            json={"license": license_key, "easypost_key": key, "orders": orders},
            timeout=8,
        )
    except Exception:
        logger.debug("Click & Drop proxy sync failed", exc_info=True)
