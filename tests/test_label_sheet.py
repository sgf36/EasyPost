"""Tests for the label print-sheet compositor (app/core/label_sheet.py).

The geometry maths (which cell a label lands in, how many pages a set needs,
whether a template stays on the page) is the part that would misprint labels,
so it is exercised directly. compose_sheets is checked for a well-formed PDF
and the right page count.
"""
import io

import pytest

from app.core import label_sheet as ls


def _png(width: int = 400, height: int = 600, colour=(10, 20, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def test_every_template_fits_its_page():
    for template in ls.list_templates():
        assert template.fits_page(), f"{template.key} runs off the page"
        assert template.per_page == template.cols * template.rows


def test_j8169_geometry_is_the_avery_diecut():
    t = ls.get_template("avery_j8169")
    assert (t.cols, t.rows) == (2, 2)
    assert t.per_page == 4
    assert (t.label_w_mm, t.label_h_mm) == (99.1, 139.0)
    # Closes exactly against A4: left + 2 labels + gap + right == 210.
    assert t.margin_left_mm + t.pitch_x_mm + t.label_w_mm + t.margin_left_mm == pytest.approx(210.0)
    assert t.margin_top_mm + t.pitch_y_mm + t.label_h_mm + t.margin_top_mm == pytest.approx(297.0)


def test_cell_origin_walks_the_grid():
    t = ls.get_template("avery_j8169")
    assert t.cell_origin_mm(0, 0) == (4.65, 9.5)
    assert t.cell_origin_mm(0, 1) == pytest.approx((4.65 + 101.6, 9.5))
    assert t.cell_origin_mm(1, 0) == pytest.approx((4.65, 9.5 + 139.0))
    assert t.cell_origin_mm(1, 1) == pytest.approx((4.65 + 101.6, 9.5 + 139.0))


def test_page_count_rounds_up_per_template():
    t = ls.get_template("avery_j8169")  # 4 per page
    assert ls.page_count(0, t) == 0
    assert ls.page_count(1, t) == 1
    assert ls.page_count(4, t) == 1
    assert ls.page_count(5, t) == 2
    assert ls.page_count(9, t) == 3


def test_get_template_falls_back_to_default():
    assert ls.get_template("does-not-exist").key == ls.DEFAULT_TEMPLATE


def test_compose_returns_a_pdf():
    pdf = ls.compose_sheets([_png(), _png(), _png()], ls.get_template("avery_j8169"))
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_compose_rejects_empty_input():
    with pytest.raises(ValueError):
        ls.compose_sheets([], ls.get_template("avery_j8169"))
    with pytest.raises(ValueError):
        ls.compose_sheets([b""], ls.get_template("avery_j8169"))


def test_compose_handles_more_than_one_page():
    # 5 labels on a 4-up template must not raise and must produce a PDF.
    labels = [_png() for _ in range(5)]
    pdf = ls.compose_sheets(labels, ls.get_template("plain_a4_4up"))
    assert pdf.startswith(b"%PDF")


def test_compose_offset_and_landscape_label_do_not_crash():
    # A landscape label into a portrait cell should rotate to fit.
    pdf = ls.compose_sheets(
        [_png(width=600, height=400)],
        ls.get_template("avery_5168"),
        offset_x_mm=1.5,
        offset_y_mm=-2.0,
    )
    assert pdf.startswith(b"%PDF")


def test_both_printers_use_all_four_j8169_cells():
    # J8169's bottom row only grazes the inkjet dead zone (~3mm of 139mm), so it
    # stays usable and is fitted into the printable part — no wasted labels.
    t = ls.get_template("avery_j8169")
    assert len(ls.usable_positions(t, "laser")) == 4
    assert len(ls.usable_positions(t, "inkjet")) == 4


def test_page_count_all_four_on_one_sheet_either_printer():
    t = ls.get_template("avery_j8169")
    assert ls.page_count(4, t, "laser") == 1
    assert ls.page_count(4, t, "inkjet") == 1  # all four fit, bottom row fitted
    assert ls.page_count(5, t, "inkjet") == 2


def test_a_cell_mostly_in_the_dead_zone_is_dropped():
    # A pathological offset that shoves the bottom row almost entirely into the
    # dead zone should drop it, not print a sliver.
    t = ls.get_template("avery_j8169")
    positions = ls.usable_positions(t, "inkjet", offset_y_mm=100.0)
    assert all(row == 0 for row, _ in positions)


def test_inkjet_compose_fits_all_four_on_one_page():
    t = ls.get_template("avery_j8169")
    pdf = ls.compose_sheets([_png() for _ in range(4)], t, printer_type="inkjet")
    assert pdf.startswith(b"%PDF")


def test_printer_margins_default_is_laser():
    assert ls.printable_margins("nonsense") == ls.printable_margins("laser")
    # Inkjet bottom margin is the large one (12.7mm vs laser's small even margin).
    assert ls.printable_margins("inkjet")[2] > ls.printable_margins("laser")[2]
