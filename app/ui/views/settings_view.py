"""Settings: update stored API keys, view active mode, language, labels."""


from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from app.core.credential_store import load_credentials, save_credentials
from app.core.label_options import (
    LABEL_FORMATS,
    default_size_for,
    normalise,
    sizes_for_format,
)
from app.core.settings import load_settings, save_settings
from app.core.webhook_manager import (
    STATE_ERROR,
    STATE_RUNNING,
    STATE_STARTING,
    webhook_manager,
)
from app.i18n import SUPPORTED_LOCALES, tr
from app.ui.widgets.async_worker import run_async


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

        save_btn = QPushButton(tr("settings.save_button"))
        save_btn.clicked.connect(self._on_save)

        button_row = QHBoxLayout()
        button_row.addWidget(show_keys_btn)
        button_row.addStretch(1)
        button_row.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addWidget(self._build_label_group())
        layout.addWidget(self._build_language_group())
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

        form = QFormLayout()
        form.addRow(tr("settings.label_format_label"), self._label_format_combo)
        form.addRow(tr("settings.label_size_label"), self._label_size_combo)

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
        layout.addWidget(note)
        layout.addWidget(caveats)
        group.setLayout(layout)
        return group

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

    def refresh(self) -> None:
        creds = load_credentials()
        self._test_key_input.setText(creds.test_key or "")
        self._prod_key_input.setText(creds.production_key or "")

    def _toggle_visibility(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self._test_key_input.setEchoMode(mode)
        self._prod_key_input.setEchoMode(mode)

    def _on_save(self) -> None:
        creds = load_credentials()
        creds.test_key = self._test_key_input.text().strip() or None
        creds.production_key = self._prod_key_input.text().strip() or None
        if not creds.has_mode(creds.active_mode):
            # Active mode's key was just cleared; fall back to whichever
            # mode still has a key, if any.
            for fallback in ("test", "production"):
                if creds.has_mode(fallback):
                    creds.active_mode = fallback
                    break
        save_credentials(creds)
        QMessageBox.information(self, tr("settings.saved_title"), tr("settings.saved_body"))
