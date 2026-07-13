"""Insure a shipment that was labeled outside EasyPost (own tracking code)."""

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.services.insurance import create_standalone_insurance
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.purchase_confirm import confirm_if_production


class InsuranceView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('insurance.title')}</h2>"))
        layout.addWidget(QLabel(tr("insurance.intro_text")))
        layout.addWidget(self._build_form_group())
        layout.addStretch(1)

    def _build_form_group(self) -> QGroupBox:
        group = QGroupBox(tr("insurance.form_group_title"))
        form = QFormLayout()

        self._tracking_code_input = QLineEdit()
        self._carrier_input = QLineEdit()
        self._amount_input = QLineEdit()
        self._amount_input.setPlaceholderText(tr("insurance.amount_placeholder"))
        self._reference_input = QLineEdit()

        form.addRow(tr("insurance.tracking_code_label"), self._tracking_code_input)
        form.addRow(tr("insurance.carrier_label"), self._carrier_input)
        form.addRow(tr("insurance.declared_value_label"), self._amount_input)
        form.addRow(tr("insurance.reference_label"), self._reference_input)

        self._submit_btn = QPushButton(tr("insurance.submit_button"))
        self._submit_btn.clicked.connect(self._on_submit)

        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        group_layout.addWidget(self._submit_btn)
        group.setLayout(group_layout)
        return group

    def _on_submit(self) -> None:
        tracking_code = self._tracking_code_input.text().strip()
        carrier = self._carrier_input.text().strip()
        amount = self._amount_input.text().strip()
        reference = self._reference_input.text().strip()

        if not tracking_code or not carrier or not amount:
            QMessageBox.warning(
                self, tr("insurance.missing_info_title"), tr("insurance.missing_info_body")
            )
            return

        if not confirm_if_production(
            self, tr("insurance.confirm_purchase", amount=amount, tracking_code=tracking_code)
        ):
            return

        self._submit_btn.setEnabled(False)
        self._pending_task = run_async(
            lambda: create_standalone_insurance(
                tracking_code=tracking_code,
                carrier=carrier,
                amount=amount,
                reference=reference,
            ),
            self,
        )
        self._pending_task.succeeded.connect(self._on_success)
        self._pending_task.failed.connect(self._on_failed)

    def _on_success(self, insurance) -> None:
        self._submit_btn.setEnabled(True)
        status = getattr(insurance, "status", "unknown")
        QMessageBox.information(
            self,
            tr("insurance.purchased_title"),
            tr("insurance.purchased_body", status=status, id=insurance.id),
        )

    def _on_failed(self, exc: Exception) -> None:
        self._submit_btn.setEnabled(True)
        QMessageBox.critical(
            self, tr("insurance.error_title"), tr("insurance.purchase_failed", error=exc)
        )
