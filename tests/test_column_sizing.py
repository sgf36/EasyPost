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


def _rates_tree(header_labels, service_names, width, *, price="24.60 GBP"):
    """A rates tree shaped like the real one: carrier headers with service
    children, four columns, the last holding a Buy button."""
    from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QPushButton

    tree = QTreeWidget()
    tree.setColumnCount(4)
    tree.setHeaderLabels(header_labels)
    carrier = QTreeWidgetItem([f"Royal Mail V3 ({len(service_names)})", "", "", ""])
    tree.addTopLevelItem(carrier)
    for name in service_names:
        child = QTreeWidgetItem(carrier, ["", price, "3", ""])
        child.setText(0, name)
        tree.setItemWidget(child, 3, QPushButton("Buy"))
    carrier.setExpanded(True)
    # As the view sets it up: the Buy column is sized to its button, not
    # stretched over whatever the text columns leave.
    tree.header().setStretchLastSection(False)
    tree.setColumnWidth(3, 80)
    tree.resize(width, 300)
    tree.show()
    return tree


TAMIL_HEADERS = ["கேரியர் மற்றும் சேவை", "கட்டணம்", "மதிப்பீட்டு நாட்கள்", ""]


def _need(tree, service):
    return tree.fontMetrics().horizontalAdvance(service) + tree.indentation() * 2


def test_service_name_survives_long_headers(qapp):
    """The Tamil case: long headers starved column 0.

    Header words are translated; carrier and service names are not, so column 0
    needs the same width in every language.
    """
    from app.ui.views.create_shipment_view import _fit_rate_columns

    service = "Royal Mail 1st Class Signed For"
    tree = _rates_tree(TAMIL_HEADERS, [service, "Royal Mail 2nd Class"], 900)
    _fit_rate_columns(tree)
    assert tree.columnWidth(0) >= _need(tree, service), (
        f"service name clipped: column 0 is {tree.columnWidth(0)}px "
        f"for {_need(tree, service)}px of text"
    )


def test_the_name_outranks_the_figures_when_space_is_short(qapp):
    """The same Tamil headers in a pane too narrow for everything.

    Deliberately no absolute pixel assertion: how much fits depends on which
    fonts the machine has, which is why an earlier version of these tests
    passed locally at 430px and failed on CI. What must hold everywhere is the
    ORDER: the name is fed before the price and the days.
    """
    from app.ui.views.create_shipment_view import _fit_rate_columns

    tree = _rates_tree(TAMIL_HEADERS, ["Royal Mail 1st Class Signed For"], 430)
    _fit_rate_columns(tree)
    assert tree.columnWidth(0) > tree.columnWidth(1)
    assert tree.columnWidth(0) > tree.columnWidth(2)


def test_a_long_price_wraps_between_words_before_the_name_gives_way(qapp):
    """"Billed to account" is two long words in Tamil. When the name needs the
    room, the price column narrows to its longest word, never below it."""
    from app.ui.views.create_shipment_view import _fit_rate_columns

    billed = "கணக்கில் வசூலிக்கப்படும்"
    tree = _rates_tree(TAMIL_HEADERS, ["Royal Mail Tracked 48 Signed For Age Verification"],
                       520, price=billed)
    _fit_rate_columns(tree)
    fm = tree.fontMetrics()
    longest_word = max(fm.horizontalAdvance(w) for w in billed.split())
    assert tree.columnWidth(1) < fm.horizontalAdvance(billed)
    assert tree.columnWidth(1) >= longest_word


def test_headers_get_the_room_when_there_is_room(qapp):
    """With the names fed, left-over width un-elides the headers first."""
    from app.ui.views.create_shipment_view import _fit_rate_columns

    tree = _rates_tree(["Carrier & service", "Rate", "Est. days", ""], ["Priority"], 700)
    _fit_rate_columns(tree)
    header = tree.header()
    assert tree.columnWidth(2) >= header.sectionSizeFromContents(2).width()


def test_a_collapsed_group_does_not_claim_width(qapp):
    """Royal Mail's collapsed catalogue holds very long names nobody can see."""
    from PySide6.QtWidgets import QTreeWidgetItem
    from app.ui.views.create_shipment_view import _fit_rate_columns

    tree = _rates_tree(["Carrier & service", "Rate", "Est. days", ""], ["Priority"], 700)
    hidden = QTreeWidgetItem(["Royal Mail V3 (1)", "", "", ""])
    tree.addTopLevelItem(hidden)
    long_name = "DE Import Tracked 24 Parcel Boxable High Volume Weekend Service"
    QTreeWidgetItem(hidden, [long_name, "0.01 GBP", "", ""])
    hidden.setExpanded(False)
    _fit_rate_columns(tree)
    days_while_collapsed = tree.columnWidth(2)
    hidden.setExpanded(True)
    _fit_rate_columns(tree)
    assert tree.columnWidth(0) >= _need(tree, long_name)
    assert days_while_collapsed >= tree.columnWidth(2)


def test_the_view_actually_calls_the_sizing(qapp):
    """The wiring, not just the helper.

    Written after a mutation test: deleting the call from
    _resize_rates_tree_to_content left the helper tests green, because they each
    call _fit_rate_columns directly. A helper nothing invokes fixes nothing, and
    that is precisely the shape of bug this file exists to catch.

    _resize_rates_tree_to_content touches only self._rates_tree, so a stub with
    that one attribute exercises the real method.
    """
    from types import SimpleNamespace
    from app.ui.views.create_shipment_view import CreateShipmentView

    service = "Royal Mail 1st Class Signed For"
    tree = _rates_tree(TAMIL_HEADERS, [service, "Royal Mail 2nd Class"], 900)
    CreateShipmentView._resize_rates_tree_to_content(SimpleNamespace(_rates_tree=tree))
    assert tree.columnWidth(0) >= _need(tree, service), (
        "the view did not size its own columns: "
        f"column 0 is {tree.columnWidth(0)}px for {_need(tree, service)}px of text"
    )
