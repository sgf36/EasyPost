"""First-run setup: collect EasyPost test/production API keys.

Keys are handed straight to credential_store (DPAPI-encrypted) and never
written anywhere else. Either key may be left blank and added later from
Settings, but at least one is required to finish setup.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import MODE_PRODUCTION, MODE_TEST
from app.core.credential_store import Credentials, save_credentials
from app.i18n import tr


class SetupWizard(QWidget):
    setup_complete = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        title = QLabel(tr("setup_wizard.title"))
        subtitle = QLabel(tr("setup_wizard.subtitle"))
        subtitle.setWordWrap(True)

        self._test_key_input = QLineEdit()
        self._test_key_input.setPlaceholderText(tr("setup_wizard.test_key_placeholder"))
        self._test_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        self._prod_key_input = QLineEdit()
        self._prod_key_input.setPlaceholderText(tr("setup_wizard.prod_key_placeholder"))
        self._prod_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        show_keys_btn = QPushButton(tr("setup_wizard.show_keys_button"))
        show_keys_btn.setCheckable(True)
        show_keys_btn.toggled.connect(self._toggle_visibility)

        form = QFormLayout()
        form.addRow(tr("setup_wizard.test_key_label"), self._test_key_input)
        form.addRow(tr("setup_wizard.prod_key_label"), self._prod_key_input)

        continue_btn = QPushButton(tr("setup_wizard.continue_button"))
        continue_btn.clicked.connect(self._on_continue)

        button_row = QHBoxLayout()
        button_row.addWidget(show_keys_btn)
        button_row.addStretch(1)
        button_row.addWidget(continue_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addStretch(1)

    def _toggle_visibility(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self._test_key_input.setEchoMode(mode)
        self._prod_key_input.setEchoMode(mode)

    def _on_continue(self) -> None:
        test_key = self._test_key_input.text().strip()
        prod_key = self._prod_key_input.text().strip()

        if not test_key and not prod_key:
            QMessageBox.warning(
                self,
                tr("setup_wizard.key_required_title"),
                tr("setup_wizard.key_required_body"),
            )
            return

        active_mode = MODE_TEST if test_key else MODE_PRODUCTION
        save_credentials(
            Credentials(
                test_key=test_key or None,
                production_key=prod_key or None,
                active_mode=active_mode,
            )
        )
        self.setup_complete.emit()
