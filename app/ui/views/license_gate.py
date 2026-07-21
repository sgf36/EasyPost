"""License activation gate shown before the EasyPost setup wizard.

Users paste the license key they were emailed after purchasing (via Paddle);
it is verified offline against the embedded public key. A "Buy a license"
button opens the Paddle checkout for users who don't have a key yet.
"""

import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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

from app.core.license import PADDLE_CHECKOUT_URL, activate
from app.i18n import is_rtl, tr

_CARD_MAX_WIDTH = 460

_CARD_STYLE = """
QFrame#licenseCard {
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: 10px;
}
"""
_ACTIVATE_BUTTON_STYLE = """
QPushButton#activateButton {
    background-color: #2b6cb0;
    color: white;
    padding: 8px 20px;
    border-radius: 6px;
    font-weight: 600;
}
QPushButton#activateButton:hover { background-color: #2c5282; }
"""


class LicenseGate(QWidget):
    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        card = QFrame()
        card.setObjectName("licenseCard")
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

        self._key_label = QLabel()
        self._key_input = QLineEdit()
        self._key_input.setMinimumHeight(30)
        self._key_input.returnPressed.connect(self._on_activate)

        self._buy_btn = QPushButton()
        self._buy_btn.setFlat(True)
        self._buy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._buy_btn.clicked.connect(self._on_buy)

        self._activate_btn = QPushButton()
        self._activate_btn.setObjectName("activateButton")
        self._activate_btn.setStyleSheet(_ACTIVATE_BUTTON_STYLE)
        self._activate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._activate_btn.clicked.connect(self._on_activate)

        button_row = QHBoxLayout()
        button_row.addWidget(self._buy_btn)
        button_row.addStretch(1)
        button_row.addWidget(self._activate_btn)

        card_layout.addWidget(self._title_label)
        card_layout.addWidget(self._subtitle_label)
        card_layout.addSpacing(4)
        card_layout.addWidget(self._key_label)
        card_layout.addWidget(self._key_input)
        card_layout.addSpacing(4)
        card_layout.addLayout(button_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        center_row = QHBoxLayout()
        center_row.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        center_row.addWidget(card)
        center_row.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        outer.addLayout(center_row)
        outer.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        self._apply_translations()

    def _apply_translations(self) -> None:
        self._title_label.setText(tr("license_gate.title"))
        self._subtitle_label.setText(tr("license_gate.subtitle"))
        self._key_label.setText(tr("license_gate.key_label"))
        self._key_input.setPlaceholderText(tr("license_gate.key_placeholder"))
        self._buy_btn.setText(tr("license_gate.buy_button"))
        self._activate_btn.setText(tr("license_gate.activate_button"))
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
        )

    def _on_buy(self) -> None:
        if PADDLE_CHECKOUT_URL:
            webbrowser.open(PADDLE_CHECKOUT_URL)
        else:
            QMessageBox.information(
                self,
                tr("license_gate.buy_soon_title"),
                tr("license_gate.buy_soon_body"),
            )

    def _on_activate(self) -> None:
        key = self._key_input.text().strip()
        if activate(key) is not None:
            self.activated.emit()
        else:
            QMessageBox.warning(
                self,
                tr("license_gate.invalid_title"),
                tr("license_gate.invalid_body"),
            )
