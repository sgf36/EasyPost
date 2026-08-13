"""File an insurance claim for a lost/damaged/stolen shipment, track status."""

from functools import partial
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import format_api_error
from app.i18n import tr
from app.services.claims import (
    CLAIM_TYPES,
    PAYMENT_METHODS,
    TYPES_REQUIRING_ATTACHMENT,
    ClaimRequestError,
    cancel_claim,
    claim_is_open,
    claim_needs_action,
    encode_attachment,
    file_claim,
    validate_claim,
    list_claims,
    refresh_claim_status,
    save_claim_locally,
)
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.purchase_confirm import confirm_if_production

_COLUMNS = ["Tracking code", "Type", "Amount", "Status", ""]
_PAYMENT_METHOD_KEYS = {
    "easypost_wallet": "claims.payment_wallet",
    "mailed_check": "claims.payment_check",
}
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
        # Wrapped, or the sentence sets the minimum width of the whole page —
        # every view sits in a QScrollArea with setWidgetResizable(True), which
        # honours a non-wrapping label's full single-line width.
        description = QLabel(tr("claims.description"))
        description.setWordWrap(True)
        layout.addWidget(description)
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
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._description_input = QLineEdit()
        self._contact_email_input = QLineEdit()
        self._recipient_name_input = QLineEdit()

        self._payment_combo = QComboBox()
        self._payment_combo.addItem(tr("claims.payment_unspecified"), userData=None)
        for method in PAYMENT_METHODS:
            self._payment_combo.addItem(tr(_PAYMENT_METHOD_KEYS[method]), userData=method)

        # Damage and theft claims are refused outright without at least one
        # document, so the files have to be collectable here.
        self._attachments: list[str] = []
        self._attachments_list = QListWidget()
        self._attachments_list.setMaximumHeight(72)
        add_file_btn = QPushButton(tr("claims.add_attachment_button"))
        add_file_btn.clicked.connect(self._on_add_attachment)
        clear_files_btn = QPushButton(tr("claims.clear_attachments_button"))
        clear_files_btn.clicked.connect(self._on_clear_attachments)
        file_buttons = QHBoxLayout()
        file_buttons.setContentsMargins(0, 0, 0, 0)
        file_buttons.addWidget(add_file_btn)
        file_buttons.addWidget(clear_files_btn)
        file_buttons.addStretch(1)
        files_box = QVBoxLayout()
        files_box.setContentsMargins(0, 0, 0, 0)
        files_box.addWidget(self._attachments_list)
        files_box.addLayout(file_buttons)
        files_widget = QWidget()
        files_widget.setLayout(files_box)

        self._attachment_note = QLabel(tr("claims.attachment_required_note"))
        self._attachment_note.setWordWrap(True)

        form.addRow(tr("claims.tracking_code_label"), self._tracking_code_input)
        form.addRow(tr("claims.type_label"), self._type_combo)
        form.addRow(tr("claims.amount_label"), self._amount_input)
        form.addRow(tr("claims.description_label"), self._description_input)
        form.addRow(tr("claims.contact_email_label"), self._contact_email_input)
        form.addRow(tr("claims.recipient_name_label"), self._recipient_name_input)
        form.addRow(tr("claims.payment_method_label"), self._payment_combo)
        form.addRow(tr("claims.attachments_label"), files_widget)
        form.addRow("", self._attachment_note)
        form.addRow("", QLabel(tr("claims.filing_window_note")))

        self._on_type_changed()

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

    def _on_type_changed(self, *_args) -> None:
        """Only damage and theft claims need documentation, so say so only when
        it applies."""
        needs = self._type_combo.currentData() in TYPES_REQUIRING_ATTACHMENT
        self._attachment_note.setVisible(needs)

    def _on_add_attachment(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("claims.add_attachment_button"),
            "",
            f"{tr('claims.attachment_filter')} (*.png *.jpg *.jpeg *.pdf *.gif *.heic)",
        )
        for path in paths:
            try:
                self._attachments.append(encode_attachment(path))
            except (OSError, ClaimRequestError) as exc:
                QMessageBox.warning(self, tr("common.error"), str(exc))
                continue
            self._attachments_list.addItem(Path(path).name)

    def _on_clear_attachments(self) -> None:
        self._attachments.clear()
        self._attachments_list.clear()

    def _on_submit(self) -> None:
        tracking_code = self._tracking_code_input.text().strip()
        amount = self._amount_input.text().strip()
        if not tracking_code or not amount:
            QMessageBox.warning(
                self, tr("claims.missing_info_title"), tr("claims.missing_info_body")
            )
            return

        claim_type = self._type_combo.currentData()
        params = dict(
            tracking_code=tracking_code,
            claim_type=claim_type,
            amount=amount,
            description=self._description_input.text().strip(),
            contact_email=self._contact_email_input.text().strip(),
            recipient_name=self._recipient_name_input.text().strip(),
            payment_method=self._payment_combo.currentData(),
            supporting_documentation_attachments=list(self._attachments),
        )

        # Everything EasyPost requires is checked before the confirmation, so a
        # missing email or document is caught here rather than as a 400 after
        # the user has committed to filing.
        try:
            validate_claim(**params)
        except ClaimRequestError as exc:
            QMessageBox.warning(self, tr("claims.missing_info_title"), str(exc))
            return

        if not confirm_if_production(
            self, tr("claims.confirm_file_body", claim_type=claim_type, amount=amount)
        ):
            return

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

            # A claim sitting in `needs_action` will never progress on its
            # own, so it is called out rather than shown as just another status.
            if claim_needs_action(rec.status):
                self._table.item(row, 3).setText(
                    tr("claims.status_needs_action", status=rec.status or "")
                )

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            check_btn = QPushButton(tr("claims.refresh_status_button"))
            check_btn.clicked.connect(partial(self._on_check_status, rec.id))
            actions_layout.addWidget(check_btn)
            cancel_btn = QPushButton(tr("claims.cancel_button"))
            # Only an open claim can be withdrawn.
            cancel_btn.setEnabled(claim_is_open(rec.status))
            cancel_btn.clicked.connect(partial(self._on_cancel_claim, rec.id))
            actions_layout.addWidget(cancel_btn)
            self._table.setCellWidget(row, len(_COLUMNS) - 1, actions)

    def _on_check_status(self, claim_id: str) -> None:
        self._pending_task = run_async(lambda: refresh_claim_status(claim_id), self)
        self._pending_task.succeeded.connect(lambda _status: self.refresh_table())
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("claims.refresh_failed_body", error=format_api_error(exc))
            )
        )

    def _on_cancel_claim(self, claim_id: str) -> None:
        """Withdraw a claim. cancel_claim() existed but nothing ever called it,
        so a claim filed by mistake could not be withdrawn from the app."""
        if QMessageBox.question(
            self, tr("claims.cancel_title"), tr("claims.cancel_body")
        ) != QMessageBox.StandardButton.Yes:
            return
        self._pending_task = run_async(lambda: cancel_claim(claim_id), self)
        self._pending_task.succeeded.connect(lambda _claim: self.refresh_table())
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"),
                tr("claims.cancel_failed_body", error=format_api_error(exc)),
            )
        )
