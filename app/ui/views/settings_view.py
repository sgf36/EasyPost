"""Settings: update stored API keys, view active mode, language, labels."""


from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import MAS_BUILD
from app.core.credential_store import load_credentials, save_credentials
from app.core.label_options import (
    LABEL_FORMATS,
    default_size_for,
    normalise,
    sizes_for_format,
)
from app.core.label_sheet import DEFAULT_PRINTER_TYPE, DEFAULT_TEMPLATE, list_templates

# The only label format that can be composed onto a sheet. A sheet is built by
# pasting label images into cells, and label_sheets._is_raster discards anything
# Pillow cannot open — which rules out PDF as well as ZPL and EPL2.
SHEET_LABEL_FORMAT = "PNG"
from app.core.settings import load_settings, save_settings
from app.core.webhook_manager import (
    STATE_ERROR,
    STATE_RUNNING,
    STATE_STARTING,
    webhook_manager,
)
from app.i18n import SUPPORTED_LOCALES, tr
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.key_verification import verify_key_slots


class SettingsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_webhook_task = None

        title = QLabel(tr("settings.title"))

        self._test_key_input = QLineEdit()
        self._test_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._prod_key_input = QLineEdit()
        self._prod_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        show_keys_btn = QPushButton(tr("settings.show_keys_button"))
        show_keys_btn.setCheckable(True)
        show_keys_btn.toggled.connect(self._toggle_visibility)

        form = QFormLayout()
        form.addRow(tr("settings.test_key_label"), self._test_key_input)
        form.addRow(tr("settings.prod_key_label"), self._prod_key_input)

        self._save_btn = QPushButton(tr("settings.save_button"))
        self._save_btn.clicked.connect(self._on_save)

        # Blank now means "leave unchanged", so removing a key has to be a
        # deliberate action rather than the side effect of an empty box.
        self._forget_btn = QPushButton(tr("settings.forget_keys_button"))
        self._forget_btn.clicked.connect(self._on_forget_keys)

        button_row = QHBoxLayout()
        button_row.addWidget(show_keys_btn)
        button_row.addWidget(self._forget_btn)
        button_row.addStretch(1)
        button_row.addWidget(self._save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addWidget(self._build_label_group())
        layout.addWidget(self._build_language_group())
        # Real-time push (webhook tunnel) is disabled on the MAS build — the App
        # Sandbox forbids the cloudflared helper (brief §4a) — so its Settings
        # section is hidden there; polling continues to keep tracking current.
        if not MAS_BUILD:
            layout.addWidget(self._build_webhook_group())
        layout.addStretch(1)

        self.refresh()

    def _build_label_group(self) -> QGroupBox:
        """Printed-label format and size.

        A preference rather than a per-shipment choice because it's dictated
        by the printer and label stock on the desk, which doesn't change from
        parcel to parcel. It applies to single and batch shipments alike, and
        only takes effect on shipments created after it's changed — EasyPost
        fixes label_size at shipment-creation time.
        """
        group = QGroupBox(tr("settings.label_group_title"))
        settings = load_settings()
        current_format, current_size = normalise(settings.label_format, settings.label_size)

        self._label_format_combo = QComboBox()
        for code in LABEL_FORMATS:
            self._label_format_combo.addItem(
                tr("settings.label_format_option", format=code, size=default_size_for(code)), code
            )
        self._label_format_combo.setCurrentIndex(self._label_format_combo.findData(current_format))

        self._label_size_combo = QComboBox()
        self._populate_label_sizes(current_format, current_size)

        self._label_format_combo.currentIndexChanged.connect(self._on_label_format_changed)
        self._label_size_combo.currentIndexChanged.connect(self._on_label_choice_saved)

        # Printer type and calibration belong here for the same reason the
        # format and size do: they describe the machine on the desk, not the
        # parcel. They were previously reachable only from the Export print
        # sheet dialog, which meant a user had to buy a label before they could
        # tell the app what printer they own.
        #
        # The dialog keeps its own copies, so a one-off sheet can still be
        # nudged without disturbing the saved default. Both read and write the
        # same AppSettings fields, so whichever is used last wins.
        self._printer_combo = QComboBox()
        self._printer_combo.addItem(tr("print_sheet.printer_laser"), "laser")
        self._printer_combo.addItem(tr("print_sheet.printer_inkjet"), "inkjet")
        index = self._printer_combo.findData(settings.printer_type or DEFAULT_PRINTER_TYPE)
        self._printer_combo.setCurrentIndex(index if index >= 0 else 0)
        self._printer_combo.currentIndexChanged.connect(self._on_printing_choice_saved)

        # The label sheet, for the same reason again: it is the stationery in
        # the drawer, not a property of the parcel. Until now it could only be
        # chosen inside the Export print sheet dialog, which cannot be opened
        # until a label has been bought — so the sheet was picked after the
        # labels were already the wrong format for it.
        #
        # Choosing one also corrects the label format, which is the point.
        # A sheet is composed by pasting label *images* into cells, so only PNG
        # can go on one: PDF, ZPL and EPL2 labels are all discarded before they
        # reach the page (see label_sheets._is_raster). Someone shipping in ZPL
        # and then exporting a print sheet got an empty one and no explanation.
        self._sheet_combo = QComboBox()
        for template in list_templates():
            self._sheet_combo.addItem(template.name, template.key)
        index = self._sheet_combo.findData(settings.label_sheet_template or DEFAULT_TEMPLATE)
        self._sheet_combo.setCurrentIndex(index if index >= 0 else 0)
        self._sheet_combo.currentIndexChanged.connect(self._on_sheet_template_changed)

        self._sheet_note = QLabel("")
        self._sheet_note.setWordWrap(True)
        self._sheet_note.setVisible(False)

        self._offset_x_spin = self._make_offset_spin(settings.label_offset_x_mm)
        self._offset_y_spin = self._make_offset_spin(settings.label_offset_y_mm)

        form = QFormLayout()
        form.addRow(tr("settings.label_format_label"), self._label_format_combo)
        form.addRow(tr("settings.label_size_label"), self._label_size_combo)
        form.addRow(tr("print_sheet.template_label"), self._sheet_combo)
        form.addRow(tr("print_sheet.printer_label"), self._printer_combo)
        form.addRow(tr("print_sheet.offset_x_label"), self._offset_x_spin)
        form.addRow(tr("print_sheet.offset_y_label"), self._offset_y_spin)

        caveats = QLabel(
            tr("settings.label_caveat_ups")
            + "\n"
            + tr("settings.label_caveat_dhl")
            + "\n"
            + tr("settings.label_caveat_zpl_only")
        )
        caveats.setWordWrap(True)

        note = QLabel(tr("settings.label_applies_note"))
        note.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._sheet_note)
        layout.addWidget(note)
        layout.addWidget(caveats)
        group.setLayout(layout)
        return group

    def _make_offset_spin(self, value: float) -> QDoubleSpinBox:
        """Millimetre nudge, matching the Export print sheet dialog's range."""
        spin = QDoubleSpinBox()
        spin.setRange(-25.0, 25.0)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setValue(value or 0.0)
        spin.setSuffix(" mm")
        spin.valueChanged.connect(self._on_printing_choice_saved)
        return spin

    def _on_sheet_template_changed(self) -> None:
        """Save the chosen sheet, and make the label format one it can use.

        Only PNG survives composition onto a sheet — PDF, ZPL and EPL2 labels
        are all dropped before they reach the page. Silently leaving an
        incompatible format selected produces an empty print sheet later, with
        nothing on screen connecting the two choices, so the format is corrected
        here and the correction is stated rather than done behind the user's
        back.
        """
        self._on_printing_choice_saved()

        current_format = self._label_format_combo.currentData()
        if current_format == SHEET_LABEL_FORMAT:
            self._sheet_note.setVisible(False)
            return

        index = self._label_format_combo.findData(SHEET_LABEL_FORMAT)
        if index < 0:
            return
        # Setting the combo runs _on_label_format_changed, which repopulates the
        # sizes and saves — so the size follows the format without being set
        # twice by two different paths.
        self._label_format_combo.setCurrentIndex(index)
        self._sheet_note.setText(
            tr("settings.sheet_needs_png_note", format=current_format)
        )
        self._sheet_note.setVisible(True)

    def _on_printing_choice_saved(self) -> None:
        """Persist the printer profile as soon as it is changed.

        Saved immediately rather than behind the Save button, which belongs to
        the API keys — pressing it is not something a user should have to do to
        make a printer choice stick.
        """
        settings = load_settings()
        settings.printer_type = self._printer_combo.currentData()
        settings.label_offset_x_mm = self._offset_x_spin.value()
        settings.label_offset_y_mm = self._offset_y_spin.value()
        settings.label_sheet_template = self._sheet_combo.currentData()
        save_settings(settings)

    def _populate_label_sizes(self, label_format: str, preferred: str) -> None:
        combo = self._label_size_combo
        combo.blockSignals(True)
        combo.clear()
        for size in sizes_for_format(label_format):
            combo.addItem(size, size)
        index = combo.findData(preferred)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _on_label_format_changed(self, _index: int) -> None:
        label_format = self._label_format_combo.currentData()
        # Re-offer only the sizes that make sense for the new format, keeping
        # the current size if it survives the switch.
        self._populate_label_sizes(label_format, self._label_size_combo.currentData())
        self._on_label_choice_saved()

    def _on_label_choice_saved(self, _index: int = 0) -> None:
        settings = load_settings()
        settings.label_format, settings.label_size = normalise(
            self._label_format_combo.currentData(), self._label_size_combo.currentData()
        )
        save_settings(settings)

    def _build_language_group(self) -> QGroupBox:
        group = QGroupBox(tr("settings.language_group_title"))

        self._language_combo = QComboBox()
        for code, _english_name, native_name in SUPPORTED_LOCALES:
            self._language_combo.addItem(native_name, code)
        current_code = load_settings().locale
        idx = self._language_combo.findData(current_code)
        if idx >= 0:
            self._language_combo.setCurrentIndex(idx)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)

        restart_note = QLabel(tr("settings.language_restart_note"))
        restart_note.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(self._language_combo)
        layout.addWidget(restart_note)
        group.setLayout(layout)
        return group

    def _on_language_changed(self, _index: int) -> None:
        code = self._language_combo.currentData()
        settings = load_settings()
        settings.locale = code
        save_settings(settings)

    def _build_webhook_group(self) -> QGroupBox:
        group = QGroupBox(tr("settings.webhook_group_title"))

        self._webhook_checkbox = QCheckBox(tr("settings.webhook_checkbox_label"))
        self._webhook_checkbox.setChecked(load_settings().webhook_enabled)
        self._webhook_checkbox.toggled.connect(self._on_webhook_toggled)

        self._webhook_status_label = QLabel()
        self._webhook_status_label.setWordWrap(True)
        webhook_manager.state_changed.connect(self._on_webhook_state_changed)
        self._update_webhook_status_label(webhook_manager.state, webhook_manager.detail)

        layout = QVBoxLayout()
        layout.addWidget(self._webhook_checkbox)
        layout.addWidget(self._webhook_status_label)
        group.setLayout(layout)
        return group

    def _on_webhook_toggled(self, checked: bool) -> None:
        if checked:
            self._pending_webhook_task = run_async(webhook_manager.start, self)
        else:
            self._pending_webhook_task = run_async(webhook_manager.stop, self)

    def _on_webhook_state_changed(self, state: str, detail: str) -> None:
        self._update_webhook_status_label(state, detail)
        self._webhook_checkbox.blockSignals(True)
        self._webhook_checkbox.setChecked(state in (STATE_RUNNING, STATE_STARTING))
        self._webhook_checkbox.blockSignals(False)

    def _update_webhook_status_label(self, state: str, detail: str) -> None:
        if state == STATE_RUNNING:
            text = tr("settings.webhook_status_running", url=detail)
        elif state == STATE_STARTING:
            text = tr("settings.webhook_status_starting")
        elif state == STATE_ERROR:
            text = tr("settings.webhook_status_error", error=detail)
        else:
            text = tr("settings.webhook_status_stopped")
        self._webhook_status_label.setText(text)

    # Shown in place of a stored key. Not a translated string on purpose: it
    # carries no language, and it must never be mistaken for the key itself.
    _STORED_MASK = "•" * 12

    def refresh(self) -> None:
        """Show whether a key is stored, never the key.

        The stored keys are deliberately NOT loaded into these fields. Putting
        a live API key into a widget means it can be read off the screen by
        anyone looking, revealed in full by the Show keys toggle, and captured
        by any screenshot, screen share or recording. A key the user has
        already saved never needs to be displayed back to them.

        An empty field therefore means "leave this key alone" on save, not
        "clear it" — see _on_save. Clearing is an explicit action.
        """
        creds = load_credentials()
        for field, stored in (
            (self._test_key_input, creds.test_key),
            (self._prod_key_input, creds.production_key),
        ):
            field.clear()
            field.setPlaceholderText(self._STORED_MASK if stored else "")

    def _toggle_visibility(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self._test_key_input.setEchoMode(mode)
        self._prod_key_input.setEchoMode(mode)

    def _on_save(self) -> None:
        test_key = self._test_key_input.text().strip()
        prod_key = self._prod_key_input.text().strip()

        # Verify each key's true mode with EasyPost before saving, so a
        # production key cannot be stored in the free test field.
        def save() -> None:
            creds = load_credentials()
            # A blank field leaves the stored key untouched. The fields start
            # empty by design (see refresh), so treating blank as "clear"
            # would wipe both keys the first time anyone opened Settings and
            # pressed Save — including someone who came here only to change
            # the label size.
            if test_key:
                creds.test_key = test_key
            if prod_key:
                creds.production_key = prod_key
            if not creds.has_mode(creds.active_mode):
                # Active mode's key was just cleared; fall back to whichever
                # mode still has a key, if any.
                for fallback in ("test", "production"):
                    if creds.has_mode(fallback):
                        creds.active_mode = fallback
                        break
            save_credentials(creds)
            QMessageBox.information(
                self, tr("settings.saved_title"), tr("settings.saved_body")
            )

        verify_key_slots(self, test_key, prod_key, on_ok=save, on_busy=self._set_keys_busy)

    def _on_forget_keys(self) -> None:
        """Remove both stored keys from this computer's credential store."""
        if QMessageBox.question(
            self,
            tr("settings.forget_keys_confirm_title"),
            tr("settings.forget_keys_confirm_body"),
        ) != QMessageBox.StandardButton.Yes:
            return
        creds = load_credentials()
        creds.test_key = None
        creds.production_key = None
        save_credentials(creds)
        self.refresh()

    def _set_keys_busy(self, busy: bool) -> None:
        self._save_btn.setEnabled(not busy)
        self._save_btn.setText(
            tr("key_check.verifying") if busy else tr("settings.save_button")
        )
