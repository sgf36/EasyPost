"""Generate a Royal Mail Sales Order Summary manifest PDF locally.

EasyPost's scan form PDF shows the API intermediary ("Easypost") as the
customer name and uses simplified service descriptions that drop the
"Signed For" qualifier.  This module generates a corrected PDF with the
actual customer name and accurate Royal Mail service descriptions while
preserving the Intersoft order reference barcode that Royal Mail scans
at collection.

The electronic manifest is still submitted to Royal Mail by EasyPost's
``scan_form.create()`` call — this module only replaces the paper
document the driver receives.
"""

from __future__ import annotations

import io
import logging
import re
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import APP_DATA_DIR

log = logging.getLogger(__name__)

MANIFESTS_DIR = APP_DATA_DIR / "manifests"

ROYAL_MAIL_SERVICES: dict[str, tuple[str, str]] = {
    "RoyalMail1stClass": ("BPL1", "Royal Mail 1st Class"),
    "RoyalMail2ndClass": ("BPL2", "Royal Mail 2nd Class"),
    "RoyalMail1stClassSignedFor": ("BPR1", "Royal Mail 1st Class Signed For"),
    "RoyalMail2ndClassSignedFor": ("BPR2", "Royal Mail 2nd Class Signed For"),
    "RoyalMail24": ("STL1", "Royal Mail Tracked 24"),
    "RoyalMail48": ("STL2", "Royal Mail Tracked 48"),
    "RoyalMailSpecialDeliveryGuaranteed1pm": ("SD1", "Special Delivery Guaranteed by 1pm"),
}


def extract_order_reference(pdf_bytes: bytes) -> Optional[str]:
    """Extract the Intersoft order reference (ISH…) from an EasyPost PDF."""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            text = page.extract_text() or ""
            match = re.search(r"ISH\d+", text)
            if match:
                return match.group()
    except Exception:
        log.warning("Could not parse EasyPost manifest PDF", exc_info=True)
    return None


def extract_account_number(pdf_bytes: bytes) -> Optional[str]:
    """Extract the Royal Mail OBA account number from an EasyPost PDF."""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            text = page.extract_text() or ""
            match = re.search(r"\b(\d{10})\b", text)
            if match:
                return match.group(1)
    except Exception:
        log.warning("Could not extract account number from PDF", exc_info=True)
    return None


def download_pdf(url: str) -> Optional[bytes]:
    """Fetch a PDF from a URL, returning None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return resp.read()
    except Exception:
        log.warning("Could not download manifest PDF from %s", url, exc_info=True)
        return None


def service_label(easypost_service: str) -> tuple[str, str]:
    """Map an EasyPost service name to (Royal Mail code, description)."""
    if easypost_service in ROYAL_MAIL_SERVICES:
        return ROYAL_MAIL_SERVICES[easypost_service]
    return ("", easypost_service)


def generate_manifest_pdf(
    *,
    order_reference: str,
    customer_name: str,
    customer_address: list[str],
    account_number: str,
    services: list[tuple[str, str, str, int]],
    collection_date: str,
    output_path: Path,
) -> Path:
    """Write a Royal Mail Sales Order Summary PDF to *output_path*.

    *services* is a list of ``(product_code, description, weight_band, count)``
    tuples — one per distinct service in the manifest.

    Returns *output_path*.
    """
    from reportlab.graphics.barcode import code128
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    output_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = A4
    c = canvas.Canvas(str(output_path), pagesize=A4)

    margin_left = 40
    margin_top = h - 50

    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin_left, margin_top, "Royal Mail")
    c.drawString(margin_left, margin_top - 24, "Sales Order Summary")
    c.drawString(margin_left, margin_top - 48, f"Order Number: {order_reference}")

    barcode = code128.Code128(order_reference, barWidth=0.8, barHeight=45)
    barcode.drawOn(c, w - 200, margin_top - 40)

    # Customer info table
    y = margin_top - 85
    left_w = 370
    right_w = 145
    row_h = 16
    total_items = sum(s[3] for s in services)
    page_count = 1

    addr_lines = customer_address[:2] if len(customer_address) >= 2 else customer_address + [""]
    right_vals = [
        ("Customer", "Collection Date"),
        (customer_name, collection_date),
        (addr_lines[0], "Series 1"),
        (addr_lines[1] if len(addr_lines) > 1 else "", f"Page 1 of {page_count}"),
    ]
    for left_val, right_val in right_vals:
        c.rect(margin_left, y - row_h, left_w, row_h)
        c.rect(margin_left + left_w, y - row_h, right_w, row_h)
        c.setFont("Helvetica", 9)
        c.drawString(margin_left + 4, y - row_h + 5, left_val)
        c.drawString(margin_left + left_w + 4, y - row_h + 5, right_val)
        y -= row_h

    y -= 20

    # Mail Centre Items header
    c.setFont("Helvetica-Bold", 10)
    header_h = 32
    c.rect(margin_left, y - header_h, left_w + right_w, header_h)
    c.drawString(margin_left + 4, y - 13, "MAIL CENTRE ITEMS")
    c.drawString(margin_left + 4, y - 27, "DOMESTIC SERVICES")
    y -= header_h

    # Services table header
    col_widths = [110, 230, 95, 80]
    row_h_header = 28
    headers = ["Account Number", "Service", "Average Weight (g)\nor Weight Band", "No of Items"]
    c.setFont("Helvetica", 9)
    for col_idx, hdr in enumerate(headers):
        cx = margin_left + sum(col_widths[:col_idx])
        c.rect(cx, y - row_h_header, col_widths[col_idx], row_h_header)
        for li, line in enumerate(hdr.split("\n")):
            c.drawString(cx + 4, y - 12 - li * 12, line)
    y -= row_h_header

    # Service data rows
    for code, description, weight_band, count in services:
        vals = [account_number, f"{code} - {description}" if code else description, weight_band, str(count)]
        for col_idx, val in enumerate(vals):
            cx = margin_left + sum(col_widths[:col_idx])
            c.rect(cx, y - row_h, col_widths[col_idx], row_h)
            if col_idx >= 2:
                tw = c.stringWidth(val, "Helvetica", 9)
                c.drawString(cx + col_widths[col_idx] - tw - 4, y - row_h + 5, val)
            else:
                c.drawString(cx + 4, y - row_h + 5, val)
        y -= row_h

    y -= 20

    # Item summary
    c.setFont("Helvetica-Bold", 10)
    c.rect(margin_left, y - row_h, left_w + right_w, row_h)
    c.drawString(margin_left + 4, y - row_h + 5, "ITEM SUMMARY")
    y -= row_h

    c.setFont("Helvetica", 9)
    for label, val in [("Number of Bags", ""), ("Number of Pouches", ""), ("Collection Date", collection_date)]:
        c.rect(margin_left, y - row_h, left_w, row_h)
        c.rect(margin_left + left_w, y - row_h, right_w, row_h)
        c.drawString(margin_left + 4, y - row_h + 5, label)
        if val:
            c.drawString(margin_left + left_w + 4, y - row_h + 5, val)
        y -= row_h

    c.setFont("Helvetica", 9)
    c.drawString(margin_left, 50, "End of Sales Order Summary")
    c.save()
    return output_path


def build_manifest_for_scan_form(
    scan_form,
    shipment_services: dict[str, str],
) -> Optional[Path]:
    """Download EasyPost's manifest, extract its reference, and generate a
    corrected PDF.

    *shipment_services* maps shipment ID → EasyPost service name (e.g.
    ``"RoyalMail2ndClassSignedFor"``).

    Returns the path to the generated PDF, or ``None`` if a corrected
    manifest could not be produced (the caller should fall back to
    ``scan_form.form_url``).
    """
    form_url = getattr(scan_form, "form_url", None)
    if not form_url:
        return None

    pdf_bytes = download_pdf(form_url)
    if not pdf_bytes:
        return None

    order_ref = extract_order_reference(pdf_bytes)
    if not order_ref:
        log.warning("No ISH order reference found — cannot generate corrected manifest")
        return None

    account_number = extract_account_number(pdf_bytes) or ""

    # Customer details from the scan form's from-address
    address = getattr(scan_form, "address", None)
    if address:
        customer_name = getattr(address, "name", None) or getattr(address, "company", None) or ""
        street = getattr(address, "street1", "") or ""
        city = getattr(address, "city", "") or ""
        state = getattr(address, "state", "") or ""
        postcode = getattr(address, "zip", "") or ""
        city_line = ", ".join(p for p in [city, state] if p)
        addr_line_2 = f"{city_line} {postcode}".strip().upper()
        customer_address = [street, addr_line_2]
    else:
        customer_name = ""
        customer_address = ["", ""]

    # Group shipments by service
    service_counts: Counter[str] = Counter()
    for svc in shipment_services.values():
        service_counts[svc] += 1

    services = []
    for svc_name, count in service_counts.items():
        code, description = service_label(svc_name)
        services.append((code, description, "1 - 100", count))

    created = getattr(scan_form, "created_at", None) or ""
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        collection_date = dt.strftime("%d/%m/%Y")
    except Exception:
        collection_date = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    output_path = MANIFESTS_DIR / f"{scan_form.id}.pdf"

    try:
        return generate_manifest_pdf(
            order_reference=order_ref,
            customer_name=customer_name,
            customer_address=customer_address,
            account_number=account_number,
            services=services,
            collection_date=collection_date,
            output_path=output_path,
        )
    except Exception:
        log.warning("Failed to generate corrected manifest PDF", exc_info=True)
        return None
