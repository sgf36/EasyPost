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

from app.core.errors import format_api_error
from app.i18n import tr
from app.services.insurance import (
    InsuranceAmountError,
    StandaloneInsuranceUnavailable,
    create_standalone_insurance,
    is_pending,
    validate_amount,
)
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

        # Validated before the purchase confirmation. The amount is always US
        # dollars and EasyPost caps it at 5,000; rejecting it here saves the
        # user confirming a spend that the API was never going to accept.
        try:
            amount = validate_amount(amount)
        except InsuranceAmountError as exc:
            QMessageBox.warning(self, tr("insurance.error_title"), str(exc))
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
        # A policy comes back `new` or `pending` and settles to `purchased` or
        # `failed` later, so this must not be announced as cover in place.
        title = (
            tr("insurance.pending_title") if is_pending(insurance)
            else tr("insurance.purchased_title")
        )
        body = (
            tr("insurance.pending_body", status=status, id=insurance.id)
            if is_pending(insurance)
            else tr("insurance.purchased_body", status=status, id=insurance.id)
        )
        QMessageBox.information(self, title, body)

    def _on_failed(self, exc: Exception) -> None:
        self._submit_btn.setEnabled(True)
        # A permission on the EasyPost account, not a mistake the user made —
        # so it gets an explanation and a way forward rather than a raw error.
        if isinstance(exc, StandaloneInsuranceUnavailable):
            QMessageBox.information(
                self, tr("insurance.error_title"), tr("insurance.not_enabled_body")
            )
            return
        QMessageBox.critical(
            self, tr("insurance.error_title"), tr("insurance.purchase_failed", error=format_api_error(exc))
        )
