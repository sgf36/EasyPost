"""Settings: update stored API keys, view active mode, language, donations."""

import webbrowser

from PySide6.QtWidgets import (
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

from app.config import DONATION_URL
from app.core.credential_store import load_credentials, save_credentials
from app.core.settings import load_settings, save_settings
from app.i18n import SUPPORTED_LOCALES, tr


class SettingsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

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
        layout.addWidget(self._build_language_group())
        layout.addWidget(self._build_support_group())
        layout.addStretch(1)

        self.refresh()

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

    def _build_support_group(self) -> QGroupBox:
        group = QGroupBox(tr("settings.support_group_title"))
        support_btn = QPushButton(tr("settings.support_button"))
        support_btn.clicked.connect(lambda: webbrowser.open(DONATION_URL))
        layout = QVBoxLayout()
        layout.addWidget(support_btn)
        group.setLayout(layout)
        return group

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
