"""Rates/customs tables hold cell *widgets*, which Qt's ResizeToContents can't
measure — the Buy button and the "HTS number (optional)" header were clipping.
These verify the explicit sizing helpers fix that under a real QApplication.
"""

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget, QPushButton, QLineEdit

from app.ui.views.create_shipment_view import _size_widget_column, _fit_columns_to_widgets


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_buy_column_widens_to_fit_button(qapp):
    table = QTableWidget(1, 2)
    table.setHorizontalHeaderLabels(["service", ""])
    button = QPushButton("Buy")
    table.setCellWidget(0, 1, button)
    table.setColumnWidth(1, 4)  # start collapsed, as ResizeToContents leaves it
    _size_widget_column(table, 1)
    assert table.columnWidth(1) >= button.sizeHint().width()


def test_hts_header_not_clipped(qapp):
    table = QTableWidget(1, 2)
    table.setHorizontalHeaderLabels(["Description", "HTS number (optional)"])
    table.setCellWidget(0, 1, QLineEdit())
    _fit_columns_to_widgets(table, stretch_col=0)
    fm = table.horizontalHeader().fontMetrics()
    assert table.columnWidth(1) >= fm.horizontalAdvance("HTS number (optional)")


def _rates_tree(header_labels, service_names, badge_text, width):
    """A rates tree shaped like the real one: carrier headers with service
    children, five columns, the last holding a Buy button."""
    from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QPushButton

    tree = QTreeWidget()
    tree.setColumnCount(5)
    tree.setHeaderLabels(header_labels)
    carrier = QTreeWidgetItem([f"Royal Mail V3 ({len(service_names)})", "", "", "", ""])
    tree.addTopLevelItem(carrier)
    for name in service_names:
        child = QTreeWidgetItem(carrier, ["", badge_text, "24.60 GBP", "3", ""])
        child.setText(0, name)
        tree.setItemWidget(child, 4, QPushButton("Buy"))
    carrier.setExpanded(True)
    tree.resize(width, 300)
    tree.show()
    return tree


def test_service_name_survives_long_headers_and_badges(qapp):
    """The Tamil case: long headers and a long badge starved column 0.

    Header words and badge text are translated; carrier and service names are
    not, so column 0 needs the same width in every language. This asserts the
    priority the fix encodes -- the service name keeps its width and the badge
    gives way -- rather than asserting particular pixel numbers.
    """
    from app.ui.views.create_shipment_view import _fit_rate_columns

    service = "Royal Mail 1st Class Signed For"
    tree = _rates_tree(
        ["கேரியர் மற்றும் சேவை", "உள்ளடக்கியது", "கட்டணம்",
         "மதிப்பீட்டு நாட்கள்", ""],
        [service, "Royal Mail 2nd Class"],
        "கண்காணிக்கப்படுகிறது",
        430,
    )
    _fit_rate_columns(tree)
    fm = tree.fontMetrics()
    need = fm.horizontalAdvance(service) + tree.indentation() * 2
    assert tree.columnWidth(0) >= need, (
        f"service name clipped: column 0 is {tree.columnWidth(0)}px "
        f"for {need}px of text"
    )


def test_english_layout_is_not_made_worse(qapp):
    """Short headers: column 0 should still take the slack, not shrink to fit."""
    from app.ui.views.create_shipment_view import _fit_rate_columns

    tree = _rates_tree(
        ["Carrier & service", "Included", "Rate", "Est. days", ""],
        ["Royal Mail 1st Class Signed For", "Royal Mail 2nd Class"],
        "Tracked",
        430,
    )
    _fit_rate_columns(tree)
    assert tree.columnWidth(0) > tree.columnWidth(1)
    assert tree.columnWidth(0) > tree.columnWidth(2)


def test_badge_yields_entirely_rather_than_clip_the_service_name(qapp):
    """At a width where both cannot fit, the badge goes, not the name.

    A floor honoured while the name clips would invert the priority this whole
    helper exists to set. "Signed For" is in the service name anyway; the badge
    repeats it.
    """
    from app.ui.views.create_shipment_view import (
        _fit_rate_columns, _RATE_BADGE_FLOOR,
    )

    service = "Royal Mail 1st Class Signed For"
    tree = _rates_tree(
        ["Carrier & service", "Included", "Rate", "Est. days", ""],
        [service],
        "Tracked",
        260,
    )
    _fit_rate_columns(tree)
    # The badge has given up its floor, and what little room exists went to the
    # name. At this width the name still cannot fit whole, and that is the
    # honest end of the ladder — but nothing else is being fed before it.
    assert tree.columnWidth(1) < _RATE_BADGE_FLOOR
    assert tree.columnWidth(0) > tree.columnWidth(1)


def test_the_view_actually_calls_the_sizing(qapp):
    """The wiring, not just the helper.

    Written after a mutation test: deleting the call from
    _resize_rates_tree_to_content left all four tests above green, because they
    each call _fit_rate_columns directly. A helper nothing invokes fixes
    nothing, and that is precisely the shape of bug this file exists to catch.

    _resize_rates_tree_to_content touches only self._rates_tree, so a stub with
    that one attribute exercises the real method.
    """
    from types import SimpleNamespace
    from app.ui.views.create_shipment_view import CreateShipmentView

    service = "Royal Mail 1st Class Signed For"
    tree = _rates_tree(
        ["கேரியர் மற்றும் சேவை", "உள்ளடக்கியது", "கட்டணம்",
         "மதிப்பீட்டு நாட்கள்", ""],
        [service, "Royal Mail 2nd Class"],
        "கண்காணிக்கப்படுகிறது",
        430,
    )
    CreateShipmentView._resize_rates_tree_to_content(
        SimpleNamespace(_rates_tree=tree)
    )
    fm = tree.fontMetrics()
    need = fm.horizontalAdvance(service) + tree.indentation() * 2
    assert tree.columnWidth(0) >= need, (
        "the view did not size its own columns: "
        f"column 0 is {tree.columnWidth(0)}px for {need}px of text"
    )
