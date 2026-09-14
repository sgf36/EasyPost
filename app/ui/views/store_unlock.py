"""Production-unlock gate for the Microsoft Store build.

The Store equivalent of the direct-download LicenseGate. There is no key to
paste: production is unlocked by buying the "Production unlock" Store add-on, so
this screen offers a Buy button (the Store's own purchase dialog), a "Restore
purchase" action for anyone who already owns it, and — since the add-on unlocks
a single computer — a link to the website for multi-computer and team licences.

It emits the same ``activated`` / ``use_test_requested`` signals as LicenseGate,
so MainWindow drives whichever gate the build calls for without caring which.
"""

import html

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from app.config import MAS_BUILD, MULTI_SEAT_URL
from app.core.license import activate
from app.i18n import is_rtl, tr
from app.ui.widgets.async_worker import run_async


def _entitlement_backend():
    """The entitlement module this build gates on. The Windows Store and the Mac
    App Store share this gate UI; each has a module with the same public surface
    (PurchaseResult / purchase_unlock / refresh_entitlement / unlock_price /
    store_listing_uri), so the gate stays build-agnostic and simply resolves
    the right one."""
    if MAS_BUILD:
        from app.core import mac_store_entitlement as backend
    else:
        from app.core import store_entitlement as backend
    return backend

# Messages that name a store, or the account you buy through. Each has a
# "_mac" twin for the Mac App Store build. Sharing one wording sent Mac
# customers to "the Microsoft Store" and "this Microsoft account", which is
# wrong for them and the kind of thing App Review rejects a build for.
STORE_NAMED_KEYS = (
    "store_unlock.buy_in_store_title",
    "store_unlock.buy_in_store_body",
    "store_unlock.error_body",
    "store_unlock.restore_none_body",
)


def store_key(key: str, mas_build: bool | None = None) -> str:
    """The catalogue key to use for ``key`` on this build."""
    if mas_build is None:
        mas_build = MAS_BUILD
    return f"{key}_mac" if (mas_build and key in STORE_NAMED_KEYS) else key


def _store_tr(key: str) -> str:
    return tr(store_key(key))


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
        self._ent = _entitlement_backend()

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

        # The price as the store formats it for this customer's country, once
        # the store has said. Hidden until then, and for good if it never does:
        # a guessed price in the wrong currency is worse than none.
        self._price_label = QLabel()
        self._price_label.setWordWrap(True)
        self._price_label.hide()
        self._price: str | None = None
        self._price_task = None
        self._price_requested = False

        self._unlock_btn = QPushButton()
        self._unlock_btn.setObjectName("unlockButton")
        self._unlock_btn.setStyleSheet(_UNLOCK_BUTTON_STYLE)
        self._unlock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unlock_btn.clicked.connect(self._on_unlock)

        self._restore_btn = QPushButton()
        self._restore_btn.setFlat(True)
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_btn.clicked.connect(self._on_restore)

        # Stacked, not side by side: in German both labels are longer than half
        # the card, and side by side each was cut off at both ends
        # ("oduktivmodus freischalte").
        button_row = QVBoxLayout()
        button_row.setSpacing(8)
        button_row.addWidget(self._unlock_btn)
        button_row.addWidget(self._restore_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Single-computer add-on → route multi-seat/org buyers to the website.
        # A word-wrapped label link (not a fixed-width button) so the longer
        # sentence wraps cleanly inside the card instead of being clipped.
        self._multi_seat_label = QLabel()
        self._multi_seat_label.setWordWrap(True)
        self._multi_seat_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._multi_seat_label.setTextFormat(Qt.TextFormat.RichText)
        self._multi_seat_label.setOpenExternalLinks(False)
        self._multi_seat_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._multi_seat_label.linkActivated.connect(self._on_multi_seat)
        if MAS_BUILD:
            # Apple permits describing volume licensing but not linking to a
            # purchase outside the App Store (MACOS-APP-STORE-PLAN.md §4), so
            # on the Mac build this is a plain sentence, not a link.
            self._multi_seat_label.setTextFormat(Qt.TextFormat.PlainText)
            self._multi_seat_label.unsetCursor()

        # An unlock code (a signed licence key) is the free-access path: the
        # developer can comp their own machine or a specific user, since the
        # Store offers no promotional code for an add-on. Verified offline.
        #
        # Hidden on MAS builds: Apple Guideline 2.4.5(vi) says Mac App Store
        # apps "may not … require license keys, or implement their own copy
        # protection", and 3.1.1 bans "license keys" as an unlock mechanism.
        # A visible "Enter code" button is exactly that. The comp-code logic
        # still works if triggered by another route (e.g. a support-guided
        # defaults-write), but must not be a discoverable UI element.
        self._code_btn = QPushButton()
        self._code_btn.setFlat(True)
        self._code_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._code_btn.clicked.connect(self._on_enter_code)
        if MAS_BUILD:
            self._code_btn.hide()

        # There is always a free way back — this screen only appears when the
        # user reaches for production.
        self._use_test_btn = QPushButton()
        self._use_test_btn.setFlat(True)
        self._use_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_test_btn.clicked.connect(self.use_test_requested.emit)

        card_layout.addWidget(self._title_label)
        card_layout.addWidget(self._subtitle_label)
        card_layout.addWidget(self._price_label)
        card_layout.addSpacing(4)
        card_layout.addLayout(button_row)
        card_layout.addWidget(self._multi_seat_label)
        card_layout.addSpacing(2)
        card_layout.addWidget(self._code_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
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
        # A heading, as on the setup screen.
        self._title_label.setText(f"<h2>{html.escape(tr('store_unlock.title'))}</h2>")
        self._subtitle_label.setText(tr("store_unlock.subtitle"))
        self._show_price()
        self._unlock_btn.setText(tr("store_unlock.unlock_button"))
        self._restore_btn.setText(tr("store_unlock.restore_button"))
        if MAS_BUILD:
            self._multi_seat_label.setText(tr("store_unlock.multi_seat_text_mac"))
        else:
            self._multi_seat_label.setText(
                f'<a href="#">{tr("store_unlock.multi_seat_link")}</a>'
            )
        self._code_btn.setText(tr("store_unlock.enter_code_button"))
        self._use_test_btn.setText(tr("store_unlock.use_test_button"))
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        # Asked for when the screen is first seen, not when the window is built,
        # so a launch that never reaches for production never calls the store.
        if not self._price_requested:
            self._price_requested = True
            self._price_task = run_async(self._ent.unlock_price, self)
            self._price_task.succeeded.connect(self._on_price)
            # A failed lookup just leaves the price off.

    def _on_price(self, price) -> None:
        self._price = price or None
        self._show_price()

    def _show_price(self) -> None:
        if self._price:
            self._price_label.setText(tr("store_unlock.price_line", price=self._price))
            self._price_label.show()
        else:
            self._price_label.hide()

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
        self._task = run_async(lambda: self._ent.purchase_unlock(hwnd), self)
        self._task.succeeded.connect(self._on_purchase_done)
        self._task.failed.connect(self._on_purchase_failed)

    def _on_purchase_done(self, result) -> None:
        self._set_busy(False)
        if result == self._ent.PurchaseResult.PURCHASED:
            self.activated.emit()
        elif result == self._ent.PurchaseResult.UNAVAILABLE:
            # In-app purchase could not be driven here: send them to the Store
            # page to buy, then they return and Restore.
            QDesktopServices.openUrl(QUrl(self._ent.store_listing_uri()))
            QMessageBox.information(
                self,
                _store_tr("store_unlock.buy_in_store_title"),
                _store_tr("store_unlock.buy_in_store_body"),
            )
        elif result == self._ent.PurchaseResult.ERROR:
            QMessageBox.warning(
                self,
                tr("store_unlock.error_title"),
                _store_tr("store_unlock.error_body"),
            )
        # NOT_PURCHASED: the user cancelled — leave the screen as-is.

    def _on_purchase_failed(self, _exc: Exception) -> None:
        self._set_busy(False)
        QMessageBox.warning(
            self, tr("store_unlock.error_title"), _store_tr("store_unlock.error_body")
        )

    def _on_restore(self) -> None:
        self._set_busy(True)
        self._task = run_async(self._ent.refresh_entitlement, self)
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
                _store_tr("store_unlock.restore_none_body"),
            )

    def _on_enter_code(self) -> None:
        """Redeem a signed unlock code (a licence key). Verified offline; on
        success production is unlocked exactly as a Store purchase would."""
        code, ok = QInputDialog.getText(
            self,
            tr("store_unlock.enter_code_title"),
            tr("store_unlock.enter_code_prompt"),
        )
        if not ok:
            return
        if activate((code or "").strip()):
            self.activated.emit()
        else:
            QMessageBox.warning(
                self,
                tr("store_unlock.enter_code_invalid_title"),
                tr("store_unlock.enter_code_invalid_body"),
            )

    def _on_multi_seat(self, _link: str = "") -> None:
        if MAS_BUILD:
            return  # plain text there; see __init__
        QDesktopServices.openUrl(QUrl(MULTI_SEAT_URL))
