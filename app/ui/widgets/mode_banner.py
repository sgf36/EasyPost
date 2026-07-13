"""Persistent banner showing the active EasyPost mode (test vs production)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from app.config import MODE_PRODUCTION, MODE_TEST
from app.core.client import client_manager
from app.core.credential_store import Credentials, save_credentials
from app.i18n import tr

_TEST_STYLE = (
    "background-color: #2b6cb0; color: white; padding: 6px 12px; "
    "font-weight: 600;"
)
_PRODUCTION_STYLE = (
    "background-color: #c53030; color: white; padding: 6px 12px; "
    "font-weight: 600;"
)


class ModeBanner(QWidget):
    """Shows which mode is active and lets the user switch, provided a key
    exists for the target mode. Emits mode_changed(str) after a switch.
    """

    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._label = QLabel()
        self._selector = QComboBox()
        self._selector.addItem(tr("mode_banner.test_mode_item"), MODE_TEST)
        self._selector.addItem(tr("mode_banner.production_mode_item"), MODE_PRODUCTION)
        self._selector.currentIndexChanged.connect(self._on_selection_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label, stretch=1)
        layout.addWidget(self._selector)

        self.refresh()

    def refresh(self) -> None:
        client_manager.reload()
        mode = client_manager.active_mode
        idx = self._selector.findData(mode)
        if idx >= 0:
            self._selector.blockSignals(True)
            self._selector.setCurrentIndex(idx)
            self._selector.blockSignals(False)
        self._apply_style(mode)

    def _apply_style(self, mode: str) -> None:
        if mode == MODE_PRODUCTION:
            self.setStyleSheet(_PRODUCTION_STYLE)
            self._label.setText(tr("mode_banner.production_label"))
        else:
            self.setStyleSheet(_TEST_STYLE)
            self._label.setText(tr("mode_banner.test_label"))

    def _on_selection_changed(self, _index: int) -> None:
        new_mode = self._selector.currentData()
        creds: Credentials = client_manager.credentials
        if not creds.has_mode(new_mode):
            # No key stored for that mode yet; revert selection.
            self.refresh()
            return
        creds.active_mode = new_mode
        save_credentials(creds)
        client_manager.reload()
        self._apply_style(new_mode)
        self.mode_changed.emit(new_mode)
