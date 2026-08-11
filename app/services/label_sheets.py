"""Fetch purchased label images and compose them into a printable sheet PDF.

Bridges the History and Batch views to app/core/label_sheet: it downloads each
shipment's hosted label (the EasyPost ``label_url``, a 4x6 PNG by default) and
hands the raw bytes to the pure compositor. The network lives here; the geometry
lives there.

A label that is not a raster image the compositor can place — a PDF-format
label, or a URL that fails to fetch — is skipped and reported, never silently
dropped, so the caller can tell the user which labels could not be included.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import requests

from app.core import label_sheet
from app.core.settings import load_settings, save_settings

LABEL_FETCH_TIMEOUT = 20


@dataclass
class SheetResult:
    pdf: bytes
    included: int  # labels placed on the sheet
    failed: list[str]  # label URLs that could not be fetched or read


def _is_raster(data: bytes) -> bool:
    """True if Pillow can open the bytes as an image (rules out PDF labels)."""
    from PIL import Image

    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def fetch_label_images(urls: list[str]) -> tuple[list[bytes], list[str]]:
    """Download each label URL. Returns (image_bytes, failed_urls)."""
    images: list[bytes] = []
    failed: list[str] = []
    for url in urls:
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=LABEL_FETCH_TIMEOUT)
            resp.raise_for_status()
            data = resp.content
        except Exception:
            failed.append(url)
            continue
        if _is_raster(data):
            images.append(data)
        else:
            failed.append(url)
    return images, failed


def remember_defaults(
    template_key: str, printer_type: str, offset_x_mm: float, offset_y_mm: float
) -> None:
    """Persist the last-used sheet choices so they pre-fill next time."""
    settings = load_settings()
    settings.label_sheet_template = template_key
    settings.printer_type = printer_type
    settings.label_offset_x_mm = offset_x_mm
    settings.label_offset_y_mm = offset_y_mm
    save_settings(settings)


def build_print_sheet(
    urls: list[str],
    *,
    template_key: str | None = None,
    printer_type: str | None = None,
    offset_x_mm: float | None = None,
    offset_y_mm: float | None = None,
) -> SheetResult:
    """Fetch the labels at ``urls`` and compose them into a print-sheet PDF.

    Any argument left as None falls back to the saved setting. Raises
    ``ValueError`` if no label could be fetched and read as an image.
    """
    settings = load_settings()
    template = label_sheet.get_template(template_key or settings.label_sheet_template)
    ptype = printer_type or settings.printer_type
    ox = settings.label_offset_x_mm if offset_x_mm is None else offset_x_mm
    oy = settings.label_offset_y_mm if offset_y_mm is None else offset_y_mm

    images, failed = fetch_label_images(urls)
    if not images:
        raise ValueError("none of the selected labels could be fetched as printable images")

    pdf = label_sheet.compose_sheets(
        images, template, printer_type=ptype, offset_x_mm=ox, offset_y_mm=oy
    )
    return SheetResult(pdf=pdf, included=len(images), failed=failed)
