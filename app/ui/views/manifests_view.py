"""Dedicated Manifests tab: create carrier manifests and browse history."""

from functools import partial

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import format_api_error
from app.i18n import tr
from app.services.formatting import display_carrier, display_service
from app.services.manifests import (
    ManifestError,
    ManifestResult,
    create_manifest,
    list_manifests,
    save_manifest_locally,
    unmanifested_shipments,
)
from app.ui.open_file import open_label
from app.ui.widgets.async_worker import run_async

_UNMANIFESTED_COLS = [
    "manifests.unmanifested_col_tracking",
    "manifests.unmanifested_col_to",
    "manifests.unmanifested_col_carrier",
    "manifests.unmanifested_col_service",
    "manifests.unmanifested_col_date",
]

_HISTORY_COLS = [
    "manifests.col_created",
    "manifests.col_shipments",
    "manifests.col_tracking_codes",
    "manifests.col_status",
    None,
]


class ManifestsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._unmanifested = []
        self._manifests = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('manifests.title')}</h2>"))
        layout.addWidget(self._build_create_group(), stretch=1)
        layout.addWidget(self._build_history_group(), stretch=1)

        self._refresh_all()

    def _build_create_group(self) -> QGroupBox:
        group = QGroupBox(tr("manifests.create_group_title"))
        headers = [tr(k) for k in _UNMANIFESTED_COLS]
        self._um_table = QTableWidget(0, len(headers))
        self._um_table.setHorizontalHeaderLabels(headers)
        header = self._um_table.horizontalHeader()
        for i in range(len(headers)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._um_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._um_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._um_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        select_all_btn = QPushButton(tr("manifests.select_all_button"))
        select_all_btn.clicked.connect(self._um_table.selectAll)
        self._create_btn = QPushButton(tr("manifests.create_button"))
        self._create_btn.clicked.connect(self._on_create_clicked)

        buttons = QHBoxLayout()
        buttons.addWidget(select_all_btn)
        buttons.addWidget(self._create_btn)
        buttons.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._um_table)
        layout.addLayout(buttons)
        group.setLayout(layout)
        return group

    def _build_history_group(self) -> QGroupBox:
        group = QGroupBox(tr("manifests.history_group_title"))
        headers = [tr(k) if k else "" for k in _HISTORY_COLS]
        self._hist_table = QTableWidget(0, len(headers))
        self._hist_table.setHorizontalHeaderLabels(headers)
        header = self._hist_table.horizontalHeader()
        for i in range(len(headers)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._hist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._hist_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        reload_btn = QPushButton(tr("manifests.reload_button"))
        reload_btn.clicked.connect(self._refresh_all)

        buttons = QHBoxLayout()
        buttons.addWidget(reload_btn)
        buttons.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._hist_table)
        layout.addLayout(buttons)
        group.setLayout(layout)
        return group

    def on_show(self) -> None:
        self._refresh_all()

    def _refresh_all(self) -> None:
        self._refresh_unmanifested()
        self._refresh_history()

    def _refresh_unmanifested(self) -> None:
        records = unmanifested_shipments()
        self._unmanifested = records
        self._um_table.setRowCount(len(records))
        for row, rec in enumerate(records):
            values = [
                rec.tracking_code or "",
                rec.to_address or "",
                display_carrier(rec.carrier or ""),
                display_service(rec.service or ""),
                (rec.created_at or "")[:10],
            ]
            for col, val in enumerate(values):
                self._um_table.setItem(row, col, QTableWidgetItem(val))
        if not records:
            self._um_table.setRowCount(1)
            item = QTableWidgetItem(tr("manifests.no_unmanifested"))
            self._um_table.setItem(0, 0, item)
            self._um_table.setSpan(0, 0, 1, len(_UNMANIFESTED_COLS))

    def _refresh_history(self) -> None:
        manifests = list_manifests()
        self._manifests = manifests
        self._hist_table.setRowCount(len(manifests))
        for row, m in enumerate(manifests):
            codes_display = ", ".join(m.tracking_codes[:5])
            if len(m.tracking_codes) > 5:
                codes_display += f" (+{len(m.tracking_codes) - 5})"
            values = [
                (m.created_at or "")[:19],
                str(m.shipment_count),
                codes_display,
                m.status or "",
            ]
            for col, val in enumerate(values):
                self._hist_table.setItem(row, col, QTableWidgetItem(val))

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            manifest_path = m.local_form_path or m.form_url
            if manifest_path:
                dl_btn = QPushButton(tr("manifests.download_button"))
                dl_btn.clicked.connect(partial(open_label, manifest_path))
                actions_layout.addWidget(dl_btn)
            self._hist_table.setCellWidget(row, len(_HISTORY_COLS) - 1, actions)

    def _on_create_clicked(self) -> None:
        rows = sorted({idx.row() for idx in self._um_table.selectionModel().selectedRows()})
        selected = [self._unmanifested[r] for r in rows if r < len(self._unmanifested)]
        if not selected:
            QMessageBox.information(
                self,
                tr("manifests.created_title"),
                tr("manifests.no_unmanifested"),
            )
            return

        carriers = {s.carrier for s in selected if s.carrier}
        if len(carriers) > 1:
            QMessageBox.warning(
                self,
                tr("manifests.created_title"),
                tr("manifests.mixed_carrier"),
            )
            return

        ids = [s.id for s in selected]
        self._create_btn.setEnabled(False)
        self._pending_task = run_async(lambda: create_manifest(ids), self)
        self._pending_task.succeeded.connect(lambda result: self._on_manifest_created(result, ids))
        self._pending_task.failed.connect(self._on_manifest_failed)

    def _on_manifest_created(self, result: ManifestResult, shipment_ids: list[str]) -> None:
        self._create_btn.setEnabled(True)
        save_manifest_locally(result.scan_form, shipment_ids, result.local_pdf_path)
        self._refresh_all()

        path_to_open = result.local_pdf_path or getattr(result.scan_form, "form_url", None)
        if path_to_open:
            reply = QMessageBox.question(
                self,
                tr("manifests.created_title"),
                tr("manifests.created_body", count=len(shipment_ids)),
                QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Close,
            )
            if reply == QMessageBox.StandardButton.Open:
                open_label(path_to_open)
        else:
            QMessageBox.information(
                self,
                tr("manifests.created_title"),
                tr("manifests.created_body", count=len(shipment_ids)),
            )

    def _on_manifest_failed(self, exc: Exception) -> None:
        self._create_btn.setEnabled(True)
        QMessageBox.critical(
            self,
            tr("manifests.created_title"),
            tr("manifests.create_failed_body", error=format_api_error(exc)),
        )
