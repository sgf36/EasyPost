"""Address book: verify a new address via EasyPost, browse saved addresses."""

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFormLayout,
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

from app.core.countries import COUNTRIES, postal_label_kind_for, state_label_kind_for
from app.i18n import tr
from app.services.addresses import (
    AddressVerificationError,
    delete_address,
    list_addresses,
    save_address_locally,
    set_favorite,
    verify_address,
)
from app.ui.widgets.async_worker import run_async

_COLUMN_COUNT = 10

_STATE_LABEL_KEYS = {
    "state": "address_book.state_field",
    "province": "address_book.state_label_province",
    "county": "address_book.state_label_county",
    "state_territory": "address_book.state_label_state_territory",
    "prefecture": "address_book.state_label_prefecture",
    "region": "address_book.state_label_region",
    "canton": "address_book.state_label_canton",
    "emirate": "address_book.state_label_emirate",
    "default": "address_book.state_label_default",
}
_POSTAL_LABEL_KEYS = {
    "zip": "address_book.postal_label_zip",
    "postal": "address_book.postal_label_postal",
}


class AddressBookView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('address_book.title')}</h2>"))

        layout.addWidget(self._build_form_group())
        layout.addWidget(self._build_table_group(), stretch=1)

        self.refresh_table()

    def _build_form_group(self) -> QGroupBox:
        group = QGroupBox(tr("address_book.form_group_title"))
        form = QFormLayout()

        self._label_input = QLineEdit()
        self._name_input = QLineEdit()
        self._company_input = QLineEdit()
        self._street1_input = QLineEdit()
        self._street2_input = QLineEdit()
        self._city_input = QLineEdit()
        self._state_input = QLineEdit()
        self._zip_input = QLineEdit()
        self._phone_input = QLineEdit()
        self._email_input = QLineEdit()
        self._favorite_checkbox = QCheckBox(tr("address_book.favorite_checkbox"))

        self._country_combo = QComboBox()
        self._country_combo.setEditable(True)
        for code, name in COUNTRIES:
            self._country_combo.addItem(name, code)
        us_index = self._country_combo.findData("US")
        if us_index >= 0:
            self._country_combo.setCurrentIndex(us_index)
        completer = self._country_combo.completer()
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._country_combo.currentIndexChanged.connect(self._on_country_changed)

        self._state_form_label = QLabel()
        self._zip_form_label = QLabel()

        form.addRow(tr("address_book.label_field"), self._label_input)
        form.addRow(tr("address_book.name_field"), self._name_input)
        form.addRow(tr("address_book.company_field"), self._company_input)
        form.addRow(tr("address_book.street1_field"), self._street1_input)
        form.addRow(tr("address_book.street2_field"), self._street2_input)
        form.addRow(tr("address_book.city_field"), self._city_input)
        form.addRow(tr("address_book.country_field"), self._country_combo)
        form.addRow(self._state_form_label, self._state_input)
        form.addRow(self._zip_form_label, self._zip_input)
        form.addRow(tr("address_book.phone_field"), self._phone_input)
        form.addRow(tr("address_book.email_field"), self._email_input)
        form.addRow("", self._favorite_checkbox)

        self._update_country_dependent_labels("US")

        self._verify_btn = QPushButton(tr("address_book.verify_save_button"))
        self._verify_btn.clicked.connect(self._on_verify_clicked)

        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        group_layout.addWidget(self._verify_btn)
        group.setLayout(group_layout)
        return group

    def _build_table_group(self) -> QGroupBox:
        group = QGroupBox(tr("address_book.table_group_title"))
        columns = [
            tr("address_book.col_label"),
            tr("address_book.col_name"),
            tr("address_book.col_street"),
            tr("address_book.col_city"),
            tr("address_book.col_state"),
            tr("address_book.col_zip"),
            tr("address_book.col_country"),
            tr("address_book.col_verified"),
            tr("address_book.col_favorite"),
            "",
        ]
        self._table = QTableWidget(0, _COLUMN_COUNT)
        self._table.setHorizontalHeaderLabels(columns)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout = QVBoxLayout()
        layout.addWidget(self._table)
        group.setLayout(layout)
        return group

    def _on_country_changed(self, _index: int) -> None:
        self._update_country_dependent_labels(self._resolve_country_code())

    def _update_country_dependent_labels(self, country_code: str) -> None:
        state_key = _STATE_LABEL_KEYS[state_label_kind_for(country_code)]
        postal_key = _POSTAL_LABEL_KEYS[postal_label_kind_for(country_code)]
        self._state_form_label.setText(tr(state_key))
        self._zip_form_label.setText(tr(postal_key))

    def _resolve_country_code(self) -> str:
        data = self._country_combo.currentData()
        if data:
            return data
        typed = self._country_combo.currentText().strip()
        # MatchFixedString is a case-insensitive exact match in Qt by default.
        idx = self._country_combo.findText(typed, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            return self._country_combo.itemData(idx)
        # Not an exact match to a known country name (e.g. user typed a raw
        # code, or a territory not in our list) — pass it through as-is and
        # let EasyPost validate it, consistent with the address-override
        # philosophy elsewhere in this form.
        return typed

    def _set_form_enabled(self, enabled: bool) -> None:
        self._verify_btn.setEnabled(enabled)
        self._verify_btn.setText(
            tr("address_book.verify_save_button") if enabled else tr("address_book.verifying_button")
        )

    def _on_verify_clicked(self) -> None:
        if not self._street1_input.text().strip() or not self._city_input.text().strip():
            QMessageBox.warning(
                self, tr("address_book.missing_info_title"), tr("address_book.missing_info_body")
            )
            return

        fields = dict(
            name=self._name_input.text().strip(),
            company=self._company_input.text().strip(),
            street1=self._street1_input.text().strip(),
            street2=self._street2_input.text().strip(),
            city=self._city_input.text().strip(),
            state=self._state_input.text().strip(),
            zip=self._zip_input.text().strip(),
            country=self._resolve_country_code() or "US",
            phone=self._phone_input.text().strip(),
            email=self._email_input.text().strip(),
        )
        label = self._label_input.text().strip() or None
        favorite = self._favorite_checkbox.isChecked()

        self._set_form_enabled(False)
        self._pending_task = run_async(lambda: verify_address(**fields), self)
        self._pending_task.succeeded.connect(
            partial(self._on_verified, label=label, favorite=favorite)
        )
        self._pending_task.failed.connect(
            partial(self._on_verify_failed, label=label, favorite=favorite)
        )

    def _on_verified(self, address, *, label, favorite) -> None:
        self._set_form_enabled(True)
        save_address_locally(address, label=label, favorite=favorite, verified=True)
        self.refresh_table()
        QMessageBox.information(
            self, tr("address_book.verified_title"), tr("address_book.verified_body")
        )

    def _on_verify_failed(self, exc: Exception, *, label, favorite) -> None:
        self._set_form_enabled(True)
        if isinstance(exc, AddressVerificationError):
            confirm_body = tr(
                "address_book.verification_failed_confirm_body",
                error="; ".join(exc.messages),
            )
            if (
                QMessageBox.question(
                    self,
                    tr("address_book.verification_failed_title"),
                    confirm_body,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            ):
                save_address_locally(exc.address, label=label, favorite=favorite, verified=False)
                self.refresh_table()
                QMessageBox.information(
                    self,
                    tr("address_book.saved_unverified_title"),
                    tr("address_book.saved_unverified_body"),
                )
        else:
            QMessageBox.critical(
                self, tr("common.error"), tr("address_book.verify_error_body", error=exc)
            )

    def refresh_table(self) -> None:
        records = list_addresses()
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            street = " ".join(filter(None, [rec.street1, rec.street2]))
            values = [
                rec.label or "",
                rec.name or "",
                street,
                rec.city or "",
                rec.state or "",
                rec.zip or "",
                rec.country or "",
                "✓" if rec.verified else "—",
                "★" if rec.is_favorite else "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, rec.id)
                self._table.setItem(row, col, item)

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)

            fav_btn = QPushButton(
                tr("address_book.unfavorite_button")
                if rec.is_favorite
                else tr("address_book.favorite_button")
            )
            fav_btn.clicked.connect(partial(self._toggle_favorite, rec.id, not rec.is_favorite))
            delete_btn = QPushButton(tr("address_book.delete_button"))
            delete_btn.clicked.connect(partial(self._delete, rec.id))

            actions_layout.addWidget(fav_btn)
            actions_layout.addWidget(delete_btn)
            self._table.setCellWidget(row, _COLUMN_COUNT - 1, actions)

    def _toggle_favorite(self, address_id: str, favorite: bool) -> None:
        set_favorite(address_id, favorite)
        self.refresh_table()

    def _delete(self, address_id: str) -> None:
        if (
            QMessageBox.question(
                self,
                tr("address_book.delete_confirm_title"),
                tr("address_book.delete_confirm_body"),
            )
            == QMessageBox.StandardButton.Yes
        ):
            delete_address(address_id)
            self.refresh_table()
