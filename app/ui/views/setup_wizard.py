"""First-run setup: pick a language, then collect EasyPost test/production
API keys.

Keys are handed straight to credential_store (OS keyring) and never written
anywhere else. Either key may be left blank and added later from Settings,
but at least one is required to finish setup.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from app.config import MODE_PRODUCTION, MODE_TEST
from app.core.credential_store import Credentials, save_credentials
from app.core.settings import load_settings, save_settings
from app.i18n import SUPPORTED_LOCALES, is_rtl, tr

_CARD_MAX_WIDTH = 460

_CARD_STYLE = """
QFrame#setupCard {
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: 10px;
}
"""
_CONTINUE_BUTTON_STYLE = """
QPushButton#continueButton {
    background-color: #2b6cb0;
    color: white;
    padding: 8px 20px;
    border-radius: 6px;
    font-weight: 600;
}
QPushButton#continueButton:hover { background-color: #2c5282; }
"""


class SetupWizard(QWidget):
    setup_complete = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        card = QFrame()
        card.setObjectName("setupCard")
        card.setStyleSheet(_CARD_STYLE)
        card.setMaximumWidth(_CARD_MAX_WIDTH)
        card.setFrameShape(QFrame.Shape.NoFrame)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 28, 32, 28)
        card_layout.setSpacing(16)

        self._title_label = QLabel()
        self._title_label.setWordWrap(True)
        self._subtitle_label = QLabel()
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setStyleSheet("color: palette(dark);")

        self._language_label = QLabel()
        self._language_combo = QComboBox()
        for code, _english_name, native_name in SUPPORTED_LOCALES:
            self._language_combo.addItem(native_name, code)
        current_code = load_settings().locale
        idx = self._language_combo.findData(current_code)
        if idx >= 0:
            self._language_combo.setCurrentIndex(idx)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)

        language_row = QHBoxLayout()
        language_row.setSpacing(10)
        language_row.addWidget(self._language_label)
        language_row.addWidget(self._language_combo, stretch=1)

        self._test_key_input = QLineEdit()
        self._test_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._test_key_input.setMinimumHeight(30)

        self._prod_key_input = QLineEdit()
        self._prod_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._prod_key_input.setMinimumHeight(30)

        self._form = QFormLayout()
        self._form.setSpacing(10)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._test_key_form_label = QLabel()
        self._prod_key_form_label = QLabel()
        self._form.addRow(self._test_key_form_label, self._test_key_input)
        self._form.addRow(self._prod_key_form_label, self._prod_key_input)

        self._show_keys_btn = QPushButton()
        self._show_keys_btn.setCheckable(True)
        self._show_keys_btn.setFlat(True)
        self._show_keys_btn.toggled.connect(self._toggle_visibility)

        self._continue_btn = QPushButton()
        self._continue_btn.setObjectName("continueButton")
        self._continue_btn.setStyleSheet(_CONTINUE_BUTTON_STYLE)
        self._continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._continue_btn.clicked.connect(self._on_continue)

        button_row = QHBoxLayout()
        button_row.addWidget(self._show_keys_btn)
        button_row.addStretch(1)
        button_row.addWidget(self._continue_btn)

        card_layout.addWidget(self._title_label)
        card_layout.addWidget(self._subtitle_label)
        card_layout.addLayout(language_row)
        card_layout.addSpacing(4)
        card_layout.addLayout(self._form)
        card_layout.addSpacing(4)
        card_layout.addLayout(button_row)

        # Center the card both horizontally and vertically in the window.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )
        center_row = QHBoxLayout()
        center_row.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        center_row.addWidget(card)
        center_row.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        outer.addLayout(center_row)
        outer.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        self._apply_translations()

    def _apply_translations(self) -> None:
        self._title_label.setText(tr("setup_wizard.title"))
        self._subtitle_label.setText(tr("setup_wizard.subtitle"))
        self._language_label.setText(tr("settings.language_group_title") + ":")
        self._test_key_input.setPlaceholderText(tr("setup_wizard.test_key_placeholder"))
        self._prod_key_input.setPlaceholderText(tr("setup_wizard.prod_key_placeholder"))
        self._test_key_form_label.setText(tr("setup_wizard.test_key_label"))
        self._prod_key_form_label.setText(tr("setup_wizard.prod_key_label"))
        self._show_keys_btn.setText(tr("setup_wizard.show_keys_button"))
        self._continue_btn.setText(tr("setup_wizard.continue_button"))
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
        )

    def _on_language_changed(self, _index: int) -> None:
        code = self._language_combo.currentData()
        settings = load_settings()
        settings.locale = code
        save_settings(settings)
        # Re-render this screen's own text immediately so the language
        # picker feels responsive; the rest of the app picks up the new
        # locale on next launch (documented in Settings).
        self._apply_translations()

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
