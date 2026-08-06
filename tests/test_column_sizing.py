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
