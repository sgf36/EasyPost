"""Compose purchased shipping labels into a printable N-up PDF "print sheet".

EasyPost labels come back at 4x6 (PNG by default). Someone without a thermal
label printer can instead print several onto a standard A4 or US Letter sheet
of peel-off labels — for example Avery L7169 / J8169 (four 99.1 x 139 mm labels
per A4) — or onto plain paper with cut guides. This module knows the die-cut
geometry of a few common sheets and lays each label image into its cell, scaled
to fit with aspect preserved and centred, writing a multi-page PDF.

The label content is raster (the EasyPost PNG) and laser printers are the
target, so the page is composited as a raster canvas with Pillow at a print
resolution (300 DPI by default). That keeps the dependency footprint to Pillow
alone and loses nothing — the source label is already a bitmap.

Pure and Qt-free so it is unit-tested headless; the UI downloads the label
bytes and hands them here.

Geometry is held in millimetres (the unit every label manufacturer publishes)
and converted to device pixels once, at compose time, for the chosen DPI.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

MM_PER_INCH = 25.4


def _mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm / MM_PER_INCH * dpi))


@dataclass(frozen=True)
class SheetTemplate:
    """The die-cut geometry of one label sheet, in millimetres.

    ``margin_left`` / ``margin_top`` are the page edge to the first label's
    top-left corner. ``pitch_x`` / ``pitch_y`` are centre-to-centre (label size
    plus the gap to the next one). A cell at grid position ``(row, col)`` has
    its top-left at ``(margin_left + col*pitch_x, margin_top + row*pitch_y)``.

    ``crop_marks`` templates are plain paper: no pre-cut labels, so short cut
    guides are drawn at each cell's corners for trimming by hand.
    """

    key: str
    name: str
    page: str  # "A4" | "Letter"
    page_w_mm: float
    page_h_mm: float
    cols: int
    rows: int
    label_w_mm: float
    label_h_mm: float
    margin_left_mm: float
    margin_top_mm: float
    pitch_x_mm: float
    pitch_y_mm: float
    corner_radius_mm: float = 0.0
    crop_marks: bool = False

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    def cell_origin_mm(self, row: int, col: int) -> tuple[float, float]:
        """Top-left of the cell at (row, col), in mm from the page's top-left."""
        return (
            self.margin_left_mm + col * self.pitch_x_mm,
            self.margin_top_mm + row * self.pitch_y_mm,
        )

    def fits_page(self) -> bool:
        """True when every cell lies within the physical page.

        A guard against a mistyped template shipping labels off the edge.
        """
        if self.cols < 1 or self.rows < 1:
            return False
        last_x = self.margin_left_mm + (self.cols - 1) * self.pitch_x_mm + self.label_w_mm
        last_y = self.margin_top_mm + (self.rows - 1) * self.pitch_y_mm + self.label_h_mm
        # Pitch must be at least the label size, or cells would overlap.
        if self.cols > 1 and self.pitch_x_mm < self.label_w_mm - 1e-6:
            return False
        if self.rows > 1 and self.pitch_y_mm < self.label_h_mm - 1e-6:
            return False
        return last_x <= self.page_w_mm + 1e-6 and last_y <= self.page_h_mm + 1e-6


# A4 and US Letter physical sizes.
_A4 = (210.0, 297.0)
_LETTER = (215.9, 279.4)


# The starter registry. Data-driven so more sheets are a one-line addition.
# Avery J8169 geometry is transcribed from the die-cut spec (label 99.1x139 mm,
# 2x2, top 9.5 / left 4.65 mm, 101.6 mm horizontal pitch = 2.5 mm gap, 139 mm
# vertical pitch = no gap) and closes exactly against A4.
TEMPLATES: dict[str, SheetTemplate] = {
    "avery_j8169": SheetTemplate(
        key="avery_j8169",
        name="Avery L7169 / J8169 — 4 per A4 (99.1 × 139 mm)",
        page="A4",
        page_w_mm=_A4[0],
        page_h_mm=_A4[1],
        cols=2,
        rows=2,
        label_w_mm=99.1,
        label_h_mm=139.0,
        margin_left_mm=4.65,
        margin_top_mm=9.5,
        pitch_x_mm=101.6,
        pitch_y_mm=139.0,
        corner_radius_mm=2.0,
    ),
    "plain_a4_4up": SheetTemplate(
        key="plain_a4_4up",
        name="Plain A4 — 4 labels per sheet (cut out)",
        page="A4",
        page_w_mm=_A4[0],
        page_h_mm=_A4[1],
        cols=2,
        rows=2,
        label_w_mm=99.1,
        label_h_mm=139.0,
        margin_left_mm=4.65,
        margin_top_mm=9.5,
        pitch_x_mm=101.6,
        pitch_y_mm=139.0,
        crop_marks=True,
    ),
    # Avery 5168: 3.5 x 5 in, 4 per US Letter, 0.5 in top/left margins, 0.5 in
    # horizontal gap (4 in pitch), no vertical gap (5 in pitch). Closes against
    # Letter (0.5 + 3.5 + 0.5 + 3.5 + 0.5 = 8.5 in; 0.5 + 5 + 5 + 0.5 = 11 in).
    "avery_5168": SheetTemplate(
        key="avery_5168",
        name="Avery 5168 — 4 per US Letter (3.5 × 5 in)",
        page="Letter",
        page_w_mm=_LETTER[0],
        page_h_mm=_LETTER[1],
        cols=2,
        rows=2,
        label_w_mm=88.9,
        label_h_mm=127.0,
        margin_left_mm=12.7,
        margin_top_mm=12.7,
        pitch_x_mm=101.6,
        pitch_y_mm=127.0,
    ),
    "plain_letter_4up": SheetTemplate(
        key="plain_letter_4up",
        name="Plain US Letter — 4 labels per sheet (cut out)",
        page="Letter",
        page_w_mm=_LETTER[0],
        page_h_mm=_LETTER[1],
        cols=2,
        rows=2,
        label_w_mm=88.9,
        label_h_mm=127.0,
        margin_left_mm=12.7,
        margin_top_mm=12.7,
        pitch_x_mm=101.6,
        pitch_y_mm=127.0,
        crop_marks=True,
    ),
}

DEFAULT_TEMPLATE = "avery_j8169"


# Printable-area margins (mm) by printer class: (top, right, bottom, left).
# Laser engines register tightly with small, even margins. Consumer inkjets
# reserve a much larger strip along the bottom edge — the trailing edge the feed
# rollers grip — and physically cannot lay ink there, which is why a full-sheet
# label's bottom row clips on an inkjet. These are deliberately conservative so
# a label is only ever placed where the printer can render it in full; a cell
# that would fall into the margin is skipped rather than clipped.
PRINTER_TYPES = ("laser", "inkjet")
_PRINTER_MARGINS_MM: dict[str, tuple[float, float, float, float]] = {
    "laser": (4.2, 4.2, 4.2, 4.2),
    "inkjet": (3.0, 3.4, 12.7, 3.4),
}
DEFAULT_PRINTER_TYPE = "laser"


def list_templates() -> list[SheetTemplate]:
    return list(TEMPLATES.values())


def get_template(key: str) -> SheetTemplate:
    return TEMPLATES.get(key) or TEMPLATES[DEFAULT_TEMPLATE]


def printable_margins(printer_type: str) -> tuple[float, float, float, float]:
    """(top, right, bottom, left) margins in mm for a printer class."""
    return _PRINTER_MARGINS_MM.get(
        (printer_type or "").lower(), _PRINTER_MARGINS_MM[DEFAULT_PRINTER_TYPE]
    )


# A cell is worth using if at least this fraction of its width AND height falls
# within the printable area. The bottom J8169 row on a DeskJet keeps ~98% of its
# height, so it stays; a cell almost entirely in the dead zone is dropped rather
# than printed as a useless sliver.
_MIN_USABLE_FRACTION = 0.5


def usable_positions(
    template: SheetTemplate,
    printer_type: str = DEFAULT_PRINTER_TYPE,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> list[tuple[int, int]]:
    """The (row, col) cells whose printable overlap is large enough to hold a
    usable label once the calibration offset is applied, in reading order.

    A cell that only grazes the printer's no-print margin (like the bottom
    J8169 row on an inkjet, ~3mm of which is unprintable) still counts — the
    label is fitted into the printable part rather than dropped. Only a cell
    that is mostly in the dead zone is skipped.
    """
    top, right, bottom, left = printable_margins(printer_type)
    px0, py0 = left, top
    px1, py1 = template.page_w_mm - right, template.page_h_mm - bottom
    positions: list[tuple[int, int]] = []
    for slot in range(template.per_page):
        row, col = divmod(slot, template.cols)
        ox, oy = template.cell_origin_mm(row, col)
        x0, y0 = ox + offset_x_mm, oy + offset_y_mm
        overlap_w = min(x0 + template.label_w_mm, px1) - max(x0, px0)
        overlap_h = min(y0 + template.label_h_mm, py1) - max(y0, py0)
        if (
            overlap_w >= _MIN_USABLE_FRACTION * template.label_w_mm
            and overlap_h >= _MIN_USABLE_FRACTION * template.label_h_mm
        ):
            positions.append((row, col))
    return positions


def positions_per_sheet(
    template: SheetTemplate,
    printer_type: str = DEFAULT_PRINTER_TYPE,
    *,
    skip_unsafe: bool = True,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> int:
    if skip_unsafe:
        return len(usable_positions(template, printer_type, offset_x_mm, offset_y_mm))
    return template.per_page


def page_count(
    n_labels: int,
    template: SheetTemplate,
    printer_type: str = DEFAULT_PRINTER_TYPE,
    *,
    skip_unsafe: bool = True,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> int:
    """How many sheets ``n_labels`` labels need, honouring the printer's safe area."""
    per = positions_per_sheet(
        template, printer_type, skip_unsafe=skip_unsafe,
        offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,
    )
    if n_labels <= 0 or per <= 0:
        return 0
    return (n_labels + per - 1) // per


def compose_sheets(
    label_images: list[bytes],
    template: SheetTemplate,
    *,
    printer_type: str = DEFAULT_PRINTER_TYPE,
    skip_unsafe: bool = True,
    dpi: int = 300,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
    rotate_to_fit: bool = True,
) -> bytes:
    """Lay the given label images onto ``template`` and return a PDF as bytes.

    Each label is scaled to fit its cell with aspect ratio preserved and
    centred, so nothing is stretched or cropped (a slight aspect mismatch just
    leaves a thin white border inside the cell). ``offset_x_mm`` / ``offset_y_mm``
    shift every label uniformly — the calibration nudge for a printer whose
    output sits a millimetre or two off the die-cut.

    ``printer_type`` selects the printable-area margins: with ``skip_unsafe``
    (the default) a cell that would fall into the printer's no-print margin is
    left empty and the label goes to the next usable cell instead, so an inkjet
    never clips a label — it simply fits fewer per sheet.

    Raises ``ValueError`` if no labels are given, or if the printer's printable
    area cannot fit a single label on this template.
    """
    pages = _compose_pages(
        label_images, template,
        printer_type=printer_type, skip_unsafe=skip_unsafe, dpi=dpi,
        offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm, rotate_to_fit=rotate_to_fit,
    )
    buf = io.BytesIO()
    pages[0].save(
        buf,
        format="PDF",
        resolution=float(dpi),
        save_all=True,
        append_images=pages[1:],
    )
    return buf.getvalue()


def preview_png(
    label_images: list[bytes],
    template: SheetTemplate,
    *,
    printer_type: str = DEFAULT_PRINTER_TYPE,
    skip_unsafe: bool = True,
    dpi: int = 150,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
    rotate_to_fit: bool = True,
) -> bytes:
    """The first composed page as PNG bytes, for an on-screen preview.

    Rendered at a lower DPI than the print PDF since it only feeds a widget.
    """
    pages = _compose_pages(
        label_images, template,
        printer_type=printer_type, skip_unsafe=skip_unsafe, dpi=dpi,
        offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm, rotate_to_fit=rotate_to_fit,
    )
    buf = io.BytesIO()
    pages[0].save(buf, format="PNG")
    return buf.getvalue()


def _compose_pages(
    label_images: list[bytes],
    template: SheetTemplate,
    *,
    printer_type: str,
    skip_unsafe: bool,
    dpi: int,
    offset_x_mm: float,
    offset_y_mm: float,
    rotate_to_fit: bool,
) -> list:
    from PIL import Image, ImageDraw

    labels = [b for b in label_images if b]
    if not labels:
        raise ValueError("no label images to compose")

    if skip_unsafe:
        positions = usable_positions(template, printer_type, offset_x_mm, offset_y_mm)
    else:
        positions = [divmod(s, template.cols) for s in range(template.per_page)]
    if not positions:
        raise ValueError(
            "the selected printer's printable area is too small to place any "
            "label on this template — try a laser printer or a different sheet"
        )

    page_w = _mm_to_px(template.page_w_mm, dpi)
    page_h = _mm_to_px(template.page_h_mm, dpi)
    cell_w = _mm_to_px(template.label_w_mm, dpi)
    cell_h = _mm_to_px(template.label_h_mm, dpi)
    off_x = _mm_to_px(offset_x_mm, dpi)
    off_y = _mm_to_px(offset_y_mm, dpi)
    tick = _mm_to_px(3.0, dpi)  # crop-mark length

    # Printable rectangle in px. With skip_unsafe the label is fitted into the
    # printable part of its cell, so a cell grazing the printer's dead zone
    # prints in full (slightly smaller) rather than clipping. Without it the
    # whole page is treated as printable and the printer clips whatever it must.
    if skip_unsafe:
        m_top, m_right, m_bottom, m_left = printable_margins(printer_type)
        pl, pt = _mm_to_px(m_left, dpi), _mm_to_px(m_top, dpi)
        pr, pb = page_w - _mm_to_px(m_right, dpi), page_h - _mm_to_px(m_bottom, dpi)
    else:
        pl, pt, pr, pb = 0, 0, page_w, page_h

    per = len(positions)
    pages: list["Image.Image"] = []

    for start in range(0, len(labels), per):
        page = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(page) if template.crop_marks else None
        for i, raw in enumerate(labels[start : start + per]):
            row, col = positions[i]
            ox_mm, oy_mm = template.cell_origin_mm(row, col)
            cx0 = _mm_to_px(ox_mm, dpi) + off_x
            cy0 = _mm_to_px(oy_mm, dpi) + off_y
            # Fit into the printable intersection of the cell (== the full cell
            # for a laser, or the cell minus the dead-zone sliver on an inkjet).
            rx0, ry0 = max(cx0, pl), max(cy0, pt)
            rx1, ry1 = min(cx0 + cell_w, pr), min(cy0 + cell_h, pb)

            _paste_label(Image, page, raw, rx0, ry0, rx1 - rx0, ry1 - ry0, rotate_to_fit)
            if draw is not None:
                _draw_crop_marks(draw, cx0, cy0, cell_w, cell_h, tick)
        pages.append(page)

    return pages


def _paste_label(Image, page, raw: bytes, x0: int, y0: int, cell_w: int, cell_h: int, rotate_to_fit: bool) -> None:
    label = Image.open(io.BytesIO(raw))
    if label.mode not in ("RGB", "RGBA", "L"):
        label = label.convert("RGB")

    # If the cell is landscape but the label portrait (or vice versa), rotating
    # the label to match fills the cell far better than letterboxing it.
    if rotate_to_fit and ((cell_w > cell_h) != (label.width > label.height)):
        label = label.rotate(90, expand=True)

    scale = min(cell_w / label.width, cell_h / label.height)
    draw_w = max(1, int(label.width * scale))
    draw_h = max(1, int(label.height * scale))
    resized = label.resize((draw_w, draw_h), Image.LANCZOS)

    px = x0 + (cell_w - draw_w) // 2
    py = y0 + (cell_h - draw_h) // 2
    mask = resized if resized.mode == "RGBA" else None
    page.paste(resized, (px, py), mask)


def _draw_crop_marks(draw, x0: int, y0: int, cell_w: int, cell_h: int, tick: int) -> None:
    corners = [
        (x0, y0, 1, 1),
        (x0 + cell_w, y0, -1, 1),
        (x0, y0 + cell_h, 1, -1),
        (x0 + cell_w, y0 + cell_h, -1, -1),
    ]
    for cx, cy, sx, sy in corners:
        draw.line([(cx, cy), (cx + sx * tick, cy)], fill="black", width=1)
        draw.line([(cx, cy), (cx, cy + sy * tick)], fill="black", width=1)


# Default resolution assumed for a label image that carries no DPI metadata.
# EasyPost's Royal Mail PNGs declare 600; a bare PNG is most often 300.
_ASSUMED_LABEL_DPI = 300


def compose_label_pages(label_images: list[bytes]) -> bytes:
    """One label per page, at the label's own physical size, as a PDF.

    This is the local replacement for EasyPost's server-side batch label merge
    (``batch.label()``), which cannot be trusted with raster labels: on
    2026-08-20 a five-shipment Royal Mail batch came back as five *landscape US
    Letter* pages, each drawing the same label twice at different offsets, so
    the address block sat on top of the 1D barcode and nothing scanned. The
    labels themselves were fine — recomposing those identical PNGs here
    produced a clean sheet — so the corruption is in the merge, not the source.

    Unlike ``compose_sheets`` this does not scale, rotate or lay out anything:
    each page is exactly one label at its native size, which is what a 4x6
    thermal printer expects. Page size is derived from the image's embedded DPI
    so a 600-DPI 2358x3542 PNG becomes a 3.93x5.9 inch page rather than a
    32-inch one.

    Raises ``ValueError`` if no label images are given.
    """
    from PIL import Image

    if not label_images:
        raise ValueError("no label images to combine")

    pages: list = []
    for raw in label_images:
        label = Image.open(io.BytesIO(raw))
        dpi = label.info.get("dpi")
        # Pillow reports DPI as a float pair and rounds oddly (599.9988); a
        # missing, zero or absurd value falls back rather than producing a page
        # measured in feet.
        x_dpi = float(dpi[0]) if dpi and dpi[0] else _ASSUMED_LABEL_DPI
        if not 72 <= x_dpi <= 2400:
            x_dpi = _ASSUMED_LABEL_DPI
        if label.mode != "RGB":
            label = label.convert("RGB")
        label.info["dpi"] = (x_dpi, x_dpi)
        pages.append(label)

    buf = io.BytesIO()
    pages[0].save(
        buf,
        format="PDF",
        resolution=float(pages[0].info["dpi"][0]),
        save_all=True,
        append_images=pages[1:],
    )
    return buf.getvalue()
