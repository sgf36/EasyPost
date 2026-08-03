"""Production-unlock gate for the Microsoft Store build.

The Store equivalent of the direct-download LicenseGate. There is no key to
paste: production is unlocked by buying the "Production unlock" Store add-on, so
this screen offers a Buy button (the Store's own purchase dialog), a "Restore
purchase" action for anyone who already owns it, and — since the add-on unlocks
a single computer — a link to the website for multi-computer and team licences.

It emits the same ``activated`` / ``use_test_requested`` signals as LicenseGate,
so MainWindow drives whichever gate the build calls for without caring which.
"""

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from app.config import MULTI_SEAT_URL
from app.core.store_entitlement import (
    PurchaseResult,
    purchase_unlock,
    refresh_entitlement,
    store_listing_uri,
)
from app.i18n import is_rtl, tr
from app.ui.widgets.async_worker import run_async

_CARD_MAX_WIDTH = 460

_CARD_STYLE = """
QFrame#storeUnlockCard {
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: 10px;
}
"""
_UNLOCK_BUTTON_STYLE = """
QPushButton#unlockButton {
    background-color: #2b6cb0;
    color: white;
    padding: 8px 20px;
    border-radius: 6px;
    font-weight: 600;
}
QPushButton#unlockButton:hover { background-color: #2c5282; }
QPushButton#unlockButton:disabled { background-color: #90a4bd; }
"""


class StoreUnlockGate(QWidget):
    activated = Signal()
    use_test_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._task = None

        card = QFrame()
        card.setObjectName("storeUnlockCard")
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

        self._unlock_btn = QPushButton()
        self._unlock_btn.setObjectName("unlockButton")
        self._unlock_btn.setStyleSheet(_UNLOCK_BUTTON_STYLE)
        self._unlock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unlock_btn.clicked.connect(self._on_unlock)

        self._restore_btn = QPushButton()
        self._restore_btn.setFlat(True)
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_btn.clicked.connect(self._on_restore)

        button_row = QHBoxLayout()
        button_row.addWidget(self._restore_btn)
        button_row.addStretch(1)
        button_row.addWidget(self._unlock_btn)

        # Single-computer add-on → route multi-seat/org buyers to the website.
        self._multi_seat_btn = QPushButton()
        self._multi_seat_btn.setFlat(True)
        self._multi_seat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._multi_seat_btn.clicked.connect(self._on_multi_seat)

        # There is always a free way back — this screen only appears when the
        # user reaches for production.
        self._use_test_btn = QPushButton()
        self._use_test_btn.setFlat(True)
        self._use_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_test_btn.clicked.connect(self.use_test_requested.emit)

        card_layout.addWidget(self._title_label)
        card_layout.addWidget(self._subtitle_label)
        card_layout.addSpacing(4)
        card_layout.addLayout(button_row)
        card_layout.addWidget(self._multi_seat_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        card_layout.addSpacing(2)
        card_layout.addWidget(self._use_test_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

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
        self._title_label.setText(tr("store_unlock.title"))
        self._subtitle_label.setText(tr("store_unlock.subtitle"))
        self._unlock_btn.setText(tr("store_unlock.unlock_button"))
        self._restore_btn.setText(tr("store_unlock.restore_button"))
        self._multi_seat_btn.setText(tr("store_unlock.multi_seat_link"))
        self._use_test_btn.setText(tr("store_unlock.use_test_button"))
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
        )

    def _set_busy(self, busy: bool) -> None:
        self._unlock_btn.setEnabled(not busy)
        self._restore_btn.setEnabled(not busy)
        self._unlock_btn.setText(
            tr("store_unlock.working") if busy else tr("store_unlock.unlock_button")
        )

    def _window_handle(self) -> int:
        """HWND of the top-level window, for the Store's modal purchase dialog."""
        try:
            return int(self.window().winId())
        except Exception:
            return 0

    def _on_unlock(self) -> None:
        self._set_busy(True)
        hwnd = self._window_handle()
        self._task = run_async(lambda: purchase_unlock(hwnd), self)
        self._task.succeeded.connect(self._on_purchase_done)
        self._task.failed.connect(self._on_purchase_failed)

    def _on_purchase_done(self, result) -> None:
        self._set_busy(False)
        if result == PurchaseResult.PURCHASED:
            self.activated.emit()
        elif result == PurchaseResult.UNAVAILABLE:
            # In-app purchase could not be driven here: send them to the Store
            # page to buy, then they return and Restore.
            QDesktopServices.openUrl(QUrl(store_listing_uri()))
            QMessageBox.information(
                self,
                tr("store_unlock.buy_in_store_title"),
                tr("store_unlock.buy_in_store_body"),
            )
        elif result == PurchaseResult.ERROR:
            QMessageBox.warning(
                self,
                tr("store_unlock.error_title"),
                tr("store_unlock.error_body"),
            )
        # NOT_PURCHASED: the user cancelled — leave the screen as-is.

    def _on_purchase_failed(self, _exc: Exception) -> None:
        self._set_busy(False)
        QMessageBox.warning(
            self, tr("store_unlock.error_title"), tr("store_unlock.error_body")
        )

    def _on_restore(self) -> None:
        self._set_busy(True)
        self._task = run_async(refresh_entitlement, self)
        self._task.succeeded.connect(self._on_restore_done)
        self._task.failed.connect(self._on_purchase_failed)

    def _on_restore_done(self, unlocked) -> None:
        self._set_busy(False)
        if unlocked:
            self.activated.emit()
        else:
            QMessageBox.information(
                self,
                tr("store_unlock.restore_none_title"),
                tr("store_unlock.restore_none_body"),
            )

    def _on_multi_seat(self) -> None:
        QDesktopServices.openUrl(QUrl(MULTI_SEAT_URL))
