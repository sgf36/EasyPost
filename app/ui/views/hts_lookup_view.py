"""HTS (Harmonized Tariff Schedule) code lookup for customs declarations.

Reference tool only — not customs/legal advice. Correct classification for
a given shipment remains the shipper's responsibility.
"""

from functools import partial

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import format_api_error
from app.i18n import tr
from app.services.hts_lookup import search_hts_codes
from app.ui.widgets.async_worker import run_async

_COLUMN_COUNT = 6


class HtsLookupView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('hts_lookup.title')}</h2>"))

        intro = QLabel(tr("hts_lookup.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(self._build_search_group())
        layout.addWidget(self._build_results_group(), stretch=1)

    def _build_search_group(self) -> QGroupBox:
        group = QGroupBox(tr("hts_lookup.search_group_title"))

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("hts_lookup.search_placeholder"))
        self._search_input.returnPressed.connect(self._on_search_clicked)

        self._search_btn = QPushButton(tr("hts_lookup.search_button"))
        self._search_btn.clicked.connect(self._on_search_clicked)

        row = QHBoxLayout()
        row.addWidget(self._search_input, stretch=1)
        row.addWidget(self._search_btn)
        group.setLayout(row)
        return group

    def _build_results_group(self) -> QGroupBox:
        group = QGroupBox(tr("hts_lookup.results_group_title"))

        self._status_label = QLabel(tr("hts_lookup.status_idle"))
        self._status_label.setWordWrap(True)

        self._table = QTableWidget(0, _COLUMN_COUNT)
        self._table.setHorizontalHeaderLabels(
            [
                tr("hts_lookup.col_htsno"),
                tr("hts_lookup.col_description"),
                tr("hts_lookup.col_general_rate"),
                tr("hts_lookup.col_special_rate"),
                tr("hts_lookup.col_other_rate"),
                "",
            ]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout = QVBoxLayout()
        layout.addWidget(self._status_label)
        layout.addWidget(self._table)
        group.setLayout(layout)
        return group

    def _on_search_clicked(self) -> None:
        keyword = self._search_input.text().strip()
        if not keyword:
            return

        self._search_btn.setEnabled(False)
        self._search_btn.setText(tr("hts_lookup.searching_button"))
        self._status_label.setText(tr("hts_lookup.status_searching"))

        self._pending_task = run_async(lambda: search_hts_codes(keyword), self)
        self._pending_task.succeeded.connect(self._on_results)
        self._pending_task.failed.connect(self._on_search_failed)

    def _on_results(self, results) -> None:
        self._search_btn.setEnabled(True)
        self._search_btn.setText(tr("hts_lookup.search_button"))

        self._table.setRowCount(len(results))
        for row, r in enumerate(results):
            values = [r.htsno, r.description, r.general_rate, r.special_rate, r.other_rate]
            for col, value in enumerate(values):
                self._table.setItem(row, col, QTableWidgetItem(value))

            copy_btn = QPushButton(tr("hts_lookup.copy_button"))
            copy_btn.setEnabled(bool(r.htsno))
            copy_btn.clicked.connect(partial(self._on_copy_clicked, r.htsno))
            self._table.setCellWidget(row, _COLUMN_COUNT - 1, copy_btn)

        if not results:
            self._status_label.setText(tr("hts_lookup.status_no_results"))
        elif any(r.from_cache for r in results):
            self._status_label.setText(tr("hts_lookup.status_offline_cache"))
        else:
            self._status_label.setText(tr("hts_lookup.status_live"))

    def _on_search_failed(self, exc: Exception) -> None:
        self._search_btn.setEnabled(True)
        self._search_btn.setText(tr("hts_lookup.search_button"))
        self._status_label.setText(tr("hts_lookup.status_error", error=format_api_error(exc)))

    def _on_copy_clicked(self, htsno: str) -> None:
        QGuiApplication.clipboard().setText(htsno)
