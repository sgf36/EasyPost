"""File an insurance claim for a lost/damaged/stolen shipment, track status."""

from functools import partial

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
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
from app.i18n import tr
from app.services.claims import CLAIM_TYPES, file_claim, list_claims, refresh_claim_status, save_claim_locally
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.purchase_confirm import confirm_if_production

_COLUMNS = ["Tracking code", "Type", "Amount", "Status", ""]
_CLAIM_TYPE_KEYS = {
    "damage": "claims.type_damage",
    "loss": "claims.type_loss",
    "theft": "claims.type_theft",
}


class ClaimsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('claims.title')}</h2>"))
        layout.addWidget(QLabel(tr("claims.description")))
        layout.addWidget(self._build_form_group())
        layout.addWidget(self._build_table_group(), stretch=1)

        self.refresh_table()

    def _build_form_group(self) -> QGroupBox:
        group = QGroupBox(tr("claims.form_group_title"))
        form = QFormLayout()

        self._tracking_code_input = QLineEdit()
        self._type_combo = QComboBox()
        for claim_type in CLAIM_TYPES:
            self._type_combo.addItem(tr(_CLAIM_TYPE_KEYS[claim_type]), userData=claim_type)
        self._amount_input = QLineEdit()
        self._amount_input.setPlaceholderText(tr("claims.amount_placeholder"))
        self._description_input = QLineEdit()
        self._contact_email_input = QLineEdit()
        self._recipient_name_input = QLineEdit()

        form.addRow(tr("claims.tracking_code_label"), self._tracking_code_input)
        form.addRow(tr("claims.type_label"), self._type_combo)
        form.addRow(tr("claims.amount_label"), self._amount_input)
        form.addRow(tr("claims.description_label"), self._description_input)
        form.addRow(tr("claims.contact_email_label"), self._contact_email_input)
        form.addRow(tr("claims.recipient_name_label"), self._recipient_name_input)

        self._submit_btn = QPushButton(tr("claims.file_button"))
        self._submit_btn.clicked.connect(self._on_submit)

        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        group_layout.addWidget(self._submit_btn)
        group.setLayout(group_layout)
        return group

    def _build_table_group(self) -> QGroupBox:
        group = QGroupBox(tr("claims.table_group_title"))
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([
            tr("claims.col_tracking_code"),
            tr("claims.col_type"),
            tr("claims.col_amount"),
            tr("claims.col_status"),
            "",
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout()
        layout.addWidget(self._table)
        group.setLayout(layout)
        return group

    def _on_submit(self) -> None:
        tracking_code = self._tracking_code_input.text().strip()
        amount = self._amount_input.text().strip()
        if not tracking_code or not amount:
            QMessageBox.warning(
                self, tr("claims.missing_info_title"), tr("claims.missing_info_body")
            )
            return

        claim_type = self._type_combo.currentData()
        if not confirm_if_production(
            self, tr("claims.confirm_file_body", claim_type=claim_type, amount=amount)
        ):
            return

        params = dict(
            tracking_code=tracking_code,
            claim_type=claim_type,
            amount=amount,
            description=self._description_input.text().strip(),
            contact_email=self._contact_email_input.text().strip(),
            recipient_name=self._recipient_name_input.text().strip(),
        )
        self._submit_btn.setEnabled(False)
        self._pending_task = run_async(lambda: file_claim(**params), self)
        self._pending_task.succeeded.connect(self._on_filed)
        self._pending_task.failed.connect(self._on_failed)

    def _on_filed(self, claim) -> None:
        self._submit_btn.setEnabled(True)
        save_claim_locally(claim)
        self.refresh_table()
        QMessageBox.information(
            self,
            tr("claims.filed_title"),
            tr("claims.filed_body", status=getattr(claim, "status", "unknown")),
        )

    def _on_failed(self, exc: Exception) -> None:
        self._submit_btn.setEnabled(True)
        QMessageBox.critical(self, tr("common.error"), tr("claims.file_failed_body", error=format_api_error(exc)))

    def refresh_table(self) -> None:
        records = list_claims()
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            values = [rec.tracking_code or "", rec.type or "", rec.amount or "", rec.status or ""]
            for col, value in enumerate(values):
                self._table.setItem(row, col, QTableWidgetItem(value))

            check_btn = QPushButton(tr("claims.refresh_status_button"))
            check_btn.clicked.connect(partial(self._on_check_status, rec.id))
            self._table.setCellWidget(row, len(_COLUMNS) - 1, check_btn)

    def _on_check_status(self, claim_id: str) -> None:
        self._pending_task = run_async(lambda: refresh_claim_status(claim_id), self)
        self._pending_task.succeeded.connect(lambda _status: self.refresh_table())
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("claims.refresh_failed_body", error=format_api_error(exc))
            )
        )
