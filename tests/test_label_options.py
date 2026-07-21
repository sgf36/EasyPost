"""Label format/size selection and the options dict sent to EasyPost."""

from unittest.mock import Mock, patch

from app.core.label_options import (
    DEFAULT_LABEL_FORMAT,
    DEFAULT_LABEL_SIZE,
    LABEL_FORMATS,
    build_options,
    default_size_for,
    normalise,
    sizes_for_format,
)
from app.core.settings import AppSettings


def test_every_format_has_the_default_size_easypost_documents():
    # Straight from EasyPost's "Shipping Label Sizes" article.
    assert LABEL_FORMATS == {"PNG": "4x6", "PDF": "8.5x11", "ZPL": "4x5", "EPL": "4x5"}


def test_default_size_for_is_case_insensitive():
    assert default_size_for("pdf") == "8.5x11"
    assert default_size_for("ZPL") == "4x5"


def test_default_size_for_unknown_format_falls_back():
    assert default_size_for("SVG") == DEFAULT_LABEL_SIZE


def test_thermal_formats_do_not_offer_the_letter_sheet_size():
    # 8.5x11 is meaningless on a 4-inch thermal printer.
    for fmt in ("ZPL", "EPL"):
        assert "8.5x11" not in sizes_for_format(fmt)


def test_pdf_offers_the_letter_sheet_size_first():
    assert sizes_for_format("PDF")[0] == "8.5x11"


def test_png_offers_the_common_thermal_sizes():
    assert sizes_for_format("PNG") == ("4x6", "4x7", "4x8")


def test_normalise_repairs_a_size_that_does_not_apply_to_the_format():
    # A settings file left over from when PDF was selected, now on ZPL.
    fmt, size = normalise("ZPL", "8.5x11")
    assert (fmt, size) == ("ZPL", "4x5")


def test_normalise_keeps_a_size_that_is_still_valid():
    assert normalise("PDF", "4x6") == ("PDF", "4x6")


def test_normalise_rejects_an_unknown_format():
    fmt, size = normalise("SVG", "4x6")
    assert fmt == DEFAULT_LABEL_FORMAT
    assert size in sizes_for_format(DEFAULT_LABEL_FORMAT)


def test_normalise_handles_empty_settings():
    assert normalise("", "") == (DEFAULT_LABEL_FORMAT, DEFAULT_LABEL_SIZE)


def test_build_options_shape_matches_easypost():
    assert build_options("pdf", "4x6") == {"label_format": "PDF", "label_size": "4x6"}


def test_shipment_create_sends_the_users_label_preference():
    from app.services.shipments import create_shipment

    client = Mock()
    with patch("app.services.shipments.load_settings",
               return_value=AppSettings(label_format="PDF", label_size="8.5x11")), \
         patch("app.services.shipments.client_manager") as manager:
        manager.get_client.return_value = client
        create_shipment(to_address_id="a", from_address_id="b", weight=16, length=1, width=1, height=1)

    options = client.shipment.create.call_args.kwargs["options"]
    assert options == {"label_format": "PDF", "label_size": "8.5x11"}


def test_batch_rows_carry_the_same_label_preference():
    # label_size is fixed at creation time, so a batch has to send it too or
    # bulk labels silently come back in the default size.
    from app.services.batches import BatchRow, _row_to_shipment_params

    row = BatchRow(
        line_number=2,
        fields={
            "to_street1": "1 Main St", "to_city": "Boston", "to_state": "MA",
            "to_zip": "02110", "to_country": "US",
            "length": "10", "width": "6", "height": "4", "weight": "16",
        },
        errors=[],
    )
    with patch("app.services.shipments.load_settings",
               return_value=AppSettings(label_format="ZPL", label_size="4x8")):
        params = _row_to_shipment_params(row, "adr_1")

    assert params["options"] == {"label_format": "ZPL", "label_size": "4x8"}
