"""License activation gate shown before the EasyPost setup wizard.

Users paste the license key they were emailed after purchasing (via Paddle);
it is verified offline against the embedded public key. A "Buy a license"
button opens the Paddle checkout for users who don't have a key yet.

Since tiers cap how many computers a key covers, a valid key is no longer the
whole story: this screen also claims one of the licence's seats. Two rules
shape how that failure is handled. If the licence is full the user is shown
which computers are using it and can release one, because the alternative is
telling a paying customer "no" with no way forward. If our own server cannot
be reached the app lets them in on a time-limited grace — an outage of ours
must never look like a licensing problem of theirs.
"""

import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from app.core import activation
from app.core.license import PADDLE_CHECKOUT_URL, activate, deactivate, verify_license
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
        info = activate(key)
        if info is None:
            QMessageBox.warning(
                self,
                tr("license_gate.invalid_title"),
                tr("license_gate.invalid_body"),
            )
            return
        if self._claim_seat(key, info):
            self.activated.emit()

    def _claim_seat(self, key: str, info, allow_release: bool = True) -> bool:
        """Take one of the licence's seats. False means do not let them in."""
        try:
            activation.activate_device(key, info)
            return True

        except activation.SeatsExhausted as exc:
            if allow_release and self._offer_release(key, info, exc.devices):
                return self._claim_seat(key, info, allow_release=False)
            return False

        except activation.SubscriptionLapsed as exc:
            # Deliberately keeps the key. An annual plan that lapsed starts
            # working again the moment it is renewed, with the same key, so
            # throwing it away would only create a support request.
            QMessageBox.warning(self, tr("license_gate.lapsed_title"), str(exc))
            return False

        except activation.LicenseRevoked as exc:
            # Refunded or withdrawn. Drop the key rather than leaving it sitting
            # in settings to fail again at the next launch.
            deactivate()
            QMessageBox.warning(self, tr("license_gate.revoked_title"), str(exc))
            return False

        except activation.ActivationUnreachable:
            until = activation.start_grace()
            QMessageBox.information(
                self,
                tr("license_gate.offline_title"),
                tr("license_gate.offline_body", date=until.strftime("%d %B %Y")),
            )
            return True

        except activation.ActivationError as exc:
            QMessageBox.warning(self, tr("license_gate.activation_failed_title"), str(exc))
            return False

    def _offer_release(self, key: str, info, devices: list[dict]) -> bool:
        """Show the computers using this licence and free one. True if freed."""
        dialog = _ReleaseDialog(info, devices, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected:
            return False
        try:
            activation.release_device(key, info, dialog.selected)
            return True
        except activation.ActivationError as exc:
            QMessageBox.warning(self, tr("license_gate.release_failed_title"), str(exc))
            return False


class _ReleaseDialog(QDialog):
    """Pick a computer to release so this one can take its seat.

    The list is labelled with names the user chose, not device hashes, because
    "release one of these" is only a real choice if they can tell them apart.
    """

    def __init__(self, info, devices: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.selected = ""
        self.setWindowTitle(tr("license_gate.release_title"))

        layout = QVBoxLayout(self)
        heading = QLabel(tr("license_gate.release_body", seats=info.seats))
        heading.setWordWrap(True)
        layout.addWidget(heading)

        self._list = QListWidget()
        for device in devices:
            label = device.get("label") or tr("license_gate.unnamed_device")
            first_seen = (device.get("first_seen") or "")[:10]
            item = QListWidgetItem(f"{label}   —   {first_seen}")
            item.setData(Qt.ItemDataRole.UserRole, device.get("device", ""))
            self._list.addItem(item)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            tr("license_gate.release_confirm")
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return  # nothing chosen: keep the dialog open rather than silently doing nothing
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
