"""Tracking: add a tracking number, view status, poll for updates."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import format_api_error
from app.core.webhook_manager import webhook_manager
from app.i18n import tr
from app.services.tracking import create_tracker, list_trackers, refresh_all_trackers, save_tracker_locally
from app.ui.widgets.async_worker import run_async

_COLUMN_KEYS = [
    "tracking.column_tracking_code",
    "tracking.column_carrier",
    "tracking.column_status",
    "tracking.column_est_delivery",
    "tracking.column_last_checked",
]
_POLL_INTERVAL_MS = 5 * 60 * 1000


class TrackingView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('tracking.title')}</h2>"))
        layout.addWidget(self._build_add_group())
        layout.addWidget(self._build_table_group(), stretch=1)

        self.refresh_table()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_all)
        self._poll_timer.start(_POLL_INTERVAL_MS)

        # Instant refresh when a webhook push update lands (see
        # app/core/webhook_manager.py); polling above stays as the fallback.
        webhook_manager.tracker_updated.connect(lambda _tracking_id: self.refresh_table())

    def _build_add_group(self) -> QGroupBox:
        group = QGroupBox(tr("tracking.add_group_title"))
        self._tracking_code_input = QLineEdit()
        self._tracking_code_input.setPlaceholderText(tr("tracking.tracking_number_placeholder"))
        self._carrier_input = QLineEdit()
        self._carrier_input.setPlaceholderText(tr("tracking.carrier_placeholder"))

        add_btn = QPushButton(tr("tracking.add_button"))
        add_btn.clicked.connect(self._on_add_clicked)
        self._add_btn = add_btn

        row = QHBoxLayout()
        row.addWidget(self._tracking_code_input, stretch=1)
        row.addWidget(self._carrier_input)
        row.addWidget(add_btn)
        group.setLayout(row)
        return group

    def _build_table_group(self) -> QGroupBox:
        group = QGroupBox(tr("tracking.table_group_title"))
        columns = [tr(key) for key in _COLUMN_KEYS]
        self._table = QTableWidget(0, len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        refresh_btn = QPushButton(tr("tracking.refresh_all_button"))
        refresh_btn.clicked.connect(self._refresh_all)
        self._refresh_btn = refresh_btn

        layout = QVBoxLayout()
        layout.addWidget(self._table)
        layout.addWidget(refresh_btn)
        group.setLayout(layout)
        return group

    def _on_add_clicked(self) -> None:
        code = self._tracking_code_input.text().strip()
        carrier = self._carrier_input.text().strip()
        if not code:
            QMessageBox.warning(
                self,
                tr("tracking.missing_tracking_number_title"),
                tr("tracking.missing_tracking_number_body"),
            )
            return

        self._add_btn.setEnabled(False)
        self._pending_task = run_async(lambda: create_tracker(code, carrier), self)
        self._pending_task.succeeded.connect(self._on_tracker_created)
        self._pending_task.failed.connect(self._on_add_failed)

    def _on_tracker_created(self, tracker) -> None:
        self._add_btn.setEnabled(True)
        save_tracker_locally(tracker)
        self._tracking_code_input.clear()
        self._carrier_input.clear()
        self.refresh_table()

    def _on_add_failed(self, exc: Exception) -> None:
        self._add_btn.setEnabled(True)
        QMessageBox.critical(
            self, tr("tracking.error_title"), tr("tracking.create_tracker_failed", error=format_api_error(exc))
        )

    def _refresh_all(self) -> None:
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText(tr("tracking.refreshing_button"))
        self._pending_task = run_async(refresh_all_trackers, self)
        self._pending_task.succeeded.connect(self._on_refreshed)
        self._pending_task.failed.connect(self._on_refresh_failed)

    def _on_refreshed(self, _trackers) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("tracking.refresh_all_button"))
        self.refresh_table()

    def _on_refresh_failed(self, exc: Exception) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("tracking.refresh_all_button"))
        QMessageBox.critical(
            self, tr("tracking.error_title"), tr("tracking.refresh_trackers_failed", error=format_api_error(exc))
        )

    def refresh_table(self) -> None:
        records = list_trackers()
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            values = [
                rec.tracking_code or "",
                rec.carrier or "",
                rec.status or "",
                rec.est_delivery_date or "",
                rec.last_checked_at or "",
            ]
            for col, value in enumerate(values):
                self._table.setItem(row, col, QTableWidgetItem(value))
