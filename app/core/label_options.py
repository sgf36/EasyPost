"""Label format and size choices, per EasyPost's published matrix.

Source: https://support.easypost.com/hc/en-us/articles/360044915671-Shipping-Label-Sizes

Two shipment options drive this: ``label_format`` and ``label_size``. They are
set when the shipment is *created*, not when the label is bought — EasyPost's
regenerate-label endpoint only takes a format, so a size chosen after the fact
would be silently ignored.

Each format has a default size that applies when no size is given:

    PDF -> 8.5x11    EPL -> 4x5    ZPL -> 4x5    PNG -> 4x6

Which sizes a carrier actually honours varies, and EasyPost does not expose
this programmatically, so the caveats below are transcribed from the article
and surfaced in the UI as guidance rather than enforced as validation — a hard
allow-list would break the moment a carrier adds a size.
"""

from __future__ import annotations

from dataclasses import dataclass

# Format code -> the size EasyPost produces when label_size is omitted.
LABEL_FORMATS: dict[str, str] = {
    "PNG": "4x6",
    "PDF": "8.5x11",
    "ZPL": "4x5",
    "EPL": "4x5",
}

# Every size EasyPost documents. "" means "carrier default for the format".
LABEL_SIZES: tuple[str, ...] = ("4x6", "4x7", "4x8", "4x5", "8.5x11")

DEFAULT_LABEL_FORMAT = "PNG"
DEFAULT_LABEL_SIZE = "4x6"


@dataclass(frozen=True)
class CarrierCaveat:
    carriers: tuple[str, ...]
    key: str


# Surfaced to the user as a warning when their chosen combination is one the
# article calls out. Keys resolve through app.i18n.
CARRIER_CAVEATS: tuple[CarrierCaveat, ...] = (
    # UPS returns 4x7 unless overridden; 4x6 needs ZPL, or Support must enable
    # 4x6 PNG (domestic only). 4x7/4x8 are a 4x6 plus blank space.
    CarrierCaveat(("ups",), "settings.label_caveat_ups"),
    # DHL Express documents 4x7, 8.5x11, 4x6 and 4x8.
    CarrierCaveat(("dhlexpress", "dhl"), "settings.label_caveat_dhl"),
    # LaserShip and OnTrac support 4x6 and 4x8, ZPL only.
    CarrierCaveat(("lasership", "ontrac"), "settings.label_caveat_zpl_only"),
)


def default_size_for(label_format: str) -> str:
    """The size EasyPost falls back to when only a format is given."""
    return LABEL_FORMATS.get((label_format or "").upper(), DEFAULT_LABEL_SIZE)


def sizes_for_format(label_format: str) -> tuple[str, ...]:
    """Sizes worth offering for a format.

    ZPL/EPL are thermal formats, so the 8.5x11 sheet size is meaningless for
    them; PDF is the only one where a full page makes sense alongside the
    thermal sizes.
    """
    fmt = (label_format or "").upper()
    if fmt in ("ZPL", "EPL"):
        return ("4x5", "4x6", "4x8")
    if fmt == "PDF":
        return ("8.5x11", "4x6", "4x7", "4x8")
    return ("4x6", "4x7", "4x8")  # PNG


def normalise(label_format: str, label_size: str) -> tuple[str, str]:
    """Coerce a stored preference back into something EasyPost accepts.

    Guards against a settings file written by an older build (or hand-edited)
    naming a size that no longer applies to the chosen format.
    """
    fmt = (label_format or DEFAULT_LABEL_FORMAT).upper()
    if fmt not in LABEL_FORMATS:
        fmt = DEFAULT_LABEL_FORMAT
    size = (label_size or "").strip()
    if size not in sizes_for_format(fmt):
        size = default_size_for(fmt)
    return fmt, size


def build_options(label_format: str, label_size: str) -> dict[str, str]:
    """The ``options`` dict to attach to a shipment create call."""
    fmt, size = normalise(label_format, label_size)
    return {"label_format": fmt, "label_size": size}
