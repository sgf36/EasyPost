"""Royal Mail manifesting: create ScanForms from purchased shipments."""

from functools import partial

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import format_api_error
from app.i18n import tr
from app.services.formatting import display_carrier, display_service, display_status
from app.services.manifests import (
    create_scan_form,
    list_local_scan_forms,
    manifestable_shipment_ids,
    retrieve_scan_form,
    save_scan_form_locally,
)
from app.ui.widgets.async_worker import run_async

_TABLE_COLUMNS = 6


class ManifestsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('manifests.title')}</h2>"))
        intro = QLabel(tr("manifests.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(self._build_create_group())
        layout.addWidget(self._build_table_group(), stretch=1)

    def _build_create_group(self) -> QGroupBox:
        group = QGroupBox(tr("manifests.create_group_title"))

        self._shipments_list = QListWidget()
        self._shipments_list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        self._shipments_list.setMaximumHeight(180)

        self._select_all_btn = QPushButton(tr("manifests.select_all_button"))
        self._select_all_btn.clicked.connect(self._select_all)

        self._refresh_eligible_btn = QPushButton(tr("manifests.refresh_eligible_button"))
        self._refresh_eligible_btn.clicked.connect(self._refresh_eligible)

        self._create_btn = QPushButton(tr("manifests.create_button"))
        self._create_btn.clicked.connect(self._on_create)

        buttons = QHBoxLayout()
        buttons.addWidget(self._select_all_btn)
        buttons.addWidget(self._refresh_eligible_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._create_btn)

        group_layout = QVBoxLayout()
        group_layout.addWidget(QLabel(tr("manifests.eligible_label")))
        group_layout.addWidget(self._shipments_list)
        group_layout.addLayout(buttons)
        group.setLayout(group_layout)
        return group

    def _build_table_group(self) -> QGroupBox:
        group = QGroupBox(tr("manifests.table_group_title"))
        self._table = QTableWidget(0, _TABLE_COLUMNS)
        self._table.setHorizontalHeaderLabels([
            tr("manifests.col_id"),
            tr("manifests.col_status"),
            tr("manifests.col_address"),
            tr("manifests.col_shipments"),
            tr("manifests.col_created"),
            "",
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout()
        layout.addWidget(self._table)
        group.setLayout(layout)
        return group

    def on_show(self) -> None:
        self._refresh_eligible()
        self._refresh_table()

    def _select_all(self) -> None:
        self._shipments_list.selectAll()

    def _refresh_eligible(self) -> None:
        self._shipments_list.clear()
        try:
            rows = manifestable_shipment_ids()
        except Exception:
            return
        for row in rows:
            label = (
                f"{row.get('tracking_code', '')} — "
                f"{display_carrier(row.get('carrier', ''))} "
                f"{display_service(row.get('service', ''))}".rstrip()
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self._shipments_list.addItem(item)

    def _on_create(self) -> None:
        selected = self._shipments_list.selectedItems()
        if not selected:
            QMessageBox.warning(
                self,
                tr("manifests.no_selection_title"),
                tr("manifests.no_selection_body"),
            )
            return

        shipment_ids = [item.data(Qt.ItemDataRole.UserRole) for item in selected]

        if (
            QMessageBox.question(
                self,
                tr("manifests.confirm_title"),
                tr("manifests.confirm_body", count=len(shipment_ids)),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        self._create_btn.setEnabled(False)
        self._pending_task = run_async(
            lambda: create_scan_form(shipment_ids), self
        )
        self._pending_task.succeeded.connect(self._on_created)
        self._pending_task.failed.connect(self._on_create_failed)

    def _on_created(self, scan_form) -> None:
        self._create_btn.setEnabled(True)
        save_scan_form_locally(scan_form)
        self._refresh_eligible()
        self._refresh_table()
        status = getattr(scan_form, "status", "creating")
        QMessageBox.information(
            self,
            tr("manifests.created_title"),
            tr("manifests.created_body", status=status, id=scan_form.id),
        )

    def _on_create_failed(self, exc: Exception) -> None:
        self._create_btn.setEnabled(True)
        QMessageBox.critical(
            self,
            tr("common.error"),
            tr("manifests.create_failed_body", error=format_api_error(exc)),
        )

    def _refresh_table(self) -> None:
        records = list_local_scan_forms()
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            values = [
                rec.id or "",
                display_status(rec.status),
                rec.address or "",
                str(rec.num_shipments or 0),
                rec.created_at or "",
            ]
            for col, value in enumerate(values):
                self._table.setItem(row, col, QTableWidgetItem(value))

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)

            refresh_btn = QPushButton(tr("manifests.refresh_button"))
            refresh_btn.clicked.connect(partial(self._on_refresh_status, rec.id))
            actions_layout.addWidget(refresh_btn)

            if rec.form_url:
                pdf_btn = QPushButton(tr("manifests.view_pdf_button"))
                pdf_btn.clicked.connect(partial(self._open_pdf, rec.form_url))
                actions_layout.addWidget(pdf_btn)

            self._table.setCellWidget(row, _TABLE_COLUMNS - 1, actions)

    def _on_refresh_status(self, scan_form_id: str) -> None:
        self._pending_task = run_async(
            lambda: retrieve_scan_form(scan_form_id), self
        )
        self._pending_task.succeeded.connect(self._on_status_refreshed)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self,
                tr("common.error"),
                tr("manifests.refresh_failed_body", error=format_api_error(exc)),
            )
        )

    def _on_status_refreshed(self, scan_form) -> None:
        save_scan_form_locally(scan_form)
        self._refresh_table()

    @staticmethod
    def _open_pdf(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))
