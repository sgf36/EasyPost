"""Small coloured pills used in the rates list: carrier identity chips and
"cheapest"/"fastest" badges.

Carrier chips are deliberately **text-only** — no carrier logo artwork ships
with this app. Naming a carrier to identify the service actually being quoted
is ordinary nominative use; bundling the stylised marks (the USPS eagle, the
UPS shield, the FedEx wordmark) is a different matter and needs written
permission from each carrier. USPS grants that case-by-case and waives the fee
when the mark indicates a shipping-method option, but UPS and FedEx have no
comparable self-serve route. The colours below are our own palette rather than
the carriers' brand colours, so the chips read as part of this app instead of
as carrier branding.

If those permissions are ever obtained, only ``carrier_chip`` needs to change —
swap the QLabel for an icon plus label and every caller keeps working.
"""

from __future__ import annotations

import re
import zlib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

# (background, foreground). Muted fills with a strong text colour, so a row of
# chips stays legible against the table's alternating row shading.
_PALETTE: list[tuple[str, str]] = [
    ("#e7ecfb", "#2b4ea8"),  # blue
    ("#fdf0e2", "#8a5a12"),  # amber
    ("#efe8fb", "#5b3fa8"),  # violet
    ("#fce9e9", "#a32d2d"),  # red
    ("#e4f4ec", "#1f7a52"),  # green
    ("#fbe8f2", "#9c2c66"),  # pink
    ("#e6f3f7", "#1c6b82"),  # teal
    ("#f0f1e6", "#5f6b23"),  # olive
]

_NEUTRAL = ("#eceff3", "#5a6472")

# Pinned so the carriers a user sees every day keep one stable colour, rather
# than depending on the hash fallback. Everything else is assigned by CRC32 of
# the normalised name, which is stable across runs (unlike hash(), which Python
# randomises per process).
_PINNED: dict[str, int] = {
    "usps": 0,
    "ups": 1,
    "fedex": 2,
    "dhl": 3,
    "dhlexpress": 3,
    "dhlecommerce": 3,
    "dhlecommerceasia": 3,
    "canadapost": 4,
    "purolator": 5,
    "royalmail": 5,
    "ontrac": 6,
    "lasership": 6,
    "australiapost": 7,
}


def _normalise(carrier: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (carrier or "").lower())


def carrier_colors(carrier: str | None) -> tuple[str, str]:
    """(background, foreground) for a carrier, stable across runs."""
    key = _normalise(carrier)
    if not key:
        return _NEUTRAL
    index = _PINNED.get(key)
    if index is None:
        index = zlib.crc32(key.encode("utf-8")) % len(_PALETTE)
    return _PALETTE[index]


def carrier_chip(carrier: str | None) -> QLabel:
    """A rounded, colour-coded pill showing the carrier's name as text."""
    text = (carrier or "").strip() or "—"
    background, foreground = carrier_colors(carrier)
    chip = QLabel(text)
    chip.setObjectName("carrierChip")
    chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
    chip.setStyleSheet(
        f"#carrierChip {{ background: {background}; color: {foreground}; "
        "border-radius: 9px; padding: 2px 9px; font-weight: 600; }}"
    )
    return chip


def badge(text: str, *, tone: str = "accent") -> QLabel:
    """A small "Cheapest"/"Fastest" marker sitting beside a service name."""
    colors = {
        "accent": ("#e4f4ec", "#1f7a52"),
        "muted": _NEUTRAL,
    }
    background, foreground = colors.get(tone, colors["accent"])
    label = QLabel(text)
    label.setObjectName("rateBadge")
    label.setStyleSheet(
        f"#rateBadge {{ background: {background}; color: {foreground}; "
        "border-radius: 7px; padding: 1px 7px; font-size: 11px; font-weight: 600; }}"
    )
    return label
