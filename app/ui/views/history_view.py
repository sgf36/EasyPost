"""Shipment history: browse purchased labels, open them, request refunds."""

import webbrowser
from functools import partial

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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
from app.services.formatting import (
    display_carrier,
    display_service,
    display_status,
    format_money,
)
from app.services.insurance import (
    InsuranceAmountError,
    insure_existing_shipment,
    is_pending,
    validate_amount,
)
from app.services.shipments import (
    list_shipments,
    refund_shipment,
    refresh_refund_status,
    save_shipment_locally,
)
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.print_sheet_dialog import PrintSheetDialog
from app.ui.widgets.purchase_confirm import confirm_if_production

_COLUMN_KEYS = [
    "history.column_tracking_code",
    "history.column_to",
    "history.column_carrier",
    "history.column_service",
    "history.column_rate",
    "history.column_status",
    "history.column_insured",
    "history.column_refund_status",
    None,
]


class HistoryView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._records = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('history.title')}</h2>"))
        layout.addWidget(self._build_table_group(), stretch=1)

        self.refresh_table()

    def _build_table_group(self) -> QGroupBox:
        group = QGroupBox(tr("history.table_group_title"))
        columns = [tr(key) if key is not None else "" for key in _COLUMN_KEYS]
        self._table = QTableWidget(0, len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Stretch divides the width evenly, which gave the actions column the
        # same share as a date and clipped its buttons to "uest ref" on a
        # published store screenshot. Buttons have a right size; let them ask
        # for it and share what is left among the text columns.
        header.setSectionResizeMode(
            len(_COLUMN_KEYS) - 1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Multi-select purchased shipments to combine their labels onto one sheet.
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        reload_btn = QPushButton(tr("history.reload_button"))
        reload_btn.clicked.connect(self.refresh_table)
        export_btn = QPushButton(tr("history.export_sheet_button"))
        export_btn.clicked.connect(self._on_export_sheet)

        buttons = QHBoxLayout()
        buttons.addWidget(reload_btn)
        buttons.addWidget(export_btn)
        buttons.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._table)
        layout.addLayout(buttons)
        group.setLayout(layout)
        return group

    def _on_export_sheet(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectionModel().selectedRows()})
        urls = [
            self._records[r].label_url
            for r in rows
            if r < len(self._records) and self._records[r].label_url
        ]
        if not urls:
            QMessageBox.information(
                self,
                tr("history.export_sheet_title"),
                tr("history.export_none_selected"),
            )
            return
        PrintSheetDialog(urls, self).exec()

    def refresh_table(self) -> None:
        records = list_shipments()
        self._records = records
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            values = [
                rec.tracking_code or "",
                rec.to_address or "",
                # These four were the raw API fields: the Carrier column read
                # "RoyalMailV3", Service read "RoyalMail2ndClass" and Status
                # read "purchased", all on a published store screenshot.
                display_carrier(rec.carrier or ""),
                display_service(rec.service or ""),
                format_money(rec.rate_amount, rec.rate_currency) if rec.rate_amount else "",
                display_status(rec.status),
                rec.insured_amount or "—",
                display_status(rec.refund_status, blank="—"),
            ]
            for col, value in enumerate(values):
                self._table.setItem(row, col, QTableWidgetItem(value))

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)

            if rec.label_url:
                open_btn = QPushButton(tr("history.open_label_button"))
                open_btn.clicked.connect(partial(webbrowser.open, rec.label_url))
                actions_layout.addWidget(open_btn)

            insure_btn = QPushButton(
                tr("history.insure_button")
                if not rec.insured_amount
                else tr("history.update_insurance_button")
            )
            insure_btn.clicked.connect(partial(self._on_insure_clicked, rec.id))
            actions_layout.addWidget(insure_btn)

            if not rec.refund_status:
                refund_btn = QPushButton(tr("history.request_refund_button"))
                refund_btn.clicked.connect(partial(self._on_refund_clicked, rec.id))
                actions_layout.addWidget(refund_btn)
            elif rec.refund_status == "submitted":
                check_btn = QPushButton(tr("history.check_refund_status_button"))
                check_btn.clicked.connect(partial(self._on_check_refund_clicked, rec.id))
                actions_layout.addWidget(check_btn)

            self._table.setCellWidget(row, len(_COLUMN_KEYS) - 1, actions)

    def _on_insure_clicked(self, shipment_id: str) -> None:
        amount, ok = QInputDialog.getText(
            self, tr("history.insure_dialog_title"), tr("history.insure_dialog_prompt")
        )
        if not ok or not amount.strip():
            return

        # Checked before the purchase confirmation, not after: EasyPost rejects
        # anything above its ceiling, and discovering that only once the user
        # has agreed to spend money is the wrong order.
        try:
            amount = validate_amount(amount)
        except InsuranceAmountError as exc:
            QMessageBox.warning(self, tr("history.insure_dialog_title"), str(exc))
            return

        if not confirm_if_production(
            self, tr("history.confirm_insure_purchase", amount=amount)
        ):
            return

        self._pending_task = run_async(
            lambda: insure_existing_shipment(shipment_id, amount), self
        )
        self._pending_task.succeeded.connect(self._on_insured)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("history.error_title"), tr("history.insure_failed", error=format_api_error(exc))
            )
        )

    def _on_insured(self, shipment) -> None:
        save_shipment_locally(shipment)
        self.refresh_table()
        # Cover is arranged asynchronously, so "insured" would overstate a
        # policy that is still `pending` — say which of the two happened.
        if is_pending(shipment):
            QMessageBox.information(
                self, tr("history.insured_title"), tr("history.insurance_pending_body")
            )
        else:
            QMessageBox.information(
                self, tr("history.insured_title"), tr("history.insured_body")
            )

    def _on_refund_clicked(self, shipment_id: str) -> None:
        if not confirm_if_production(
            self, tr("history.confirm_refund_request")
        ):
            return
        self._pending_task = run_async(lambda: refund_shipment(shipment_id), self)
        self._pending_task.succeeded.connect(self._on_refund_submitted)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("history.error_title"), tr("history.refund_failed", error=format_api_error(exc))
            )
        )

    def _on_refund_submitted(self, shipment) -> None:
        save_shipment_locally(shipment)
        self.refresh_table()
        QMessageBox.information(
            self,
            tr("history.refund_submitted_title"),
            tr("history.refund_submitted_body"),
        )

    def _on_check_refund_clicked(self, shipment_id: str) -> None:
        self._pending_task = run_async(lambda: refresh_refund_status(shipment_id), self)
        self._pending_task.succeeded.connect(self._on_refund_status_checked)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("history.error_title"), tr("history.check_status_failed", error=format_api_error(exc))
            )
        )

    def _on_refund_status_checked(self, status) -> None:
        self.refresh_table()
        QMessageBox.information(
            self, tr("history.refund_status_title"), tr("history.refund_status_body", status=status)
        )
