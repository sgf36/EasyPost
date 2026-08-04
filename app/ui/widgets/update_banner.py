"""Dismissable "a newer version is available" banner (direct-download builds).

Hidden until :meth:`show_update` is called with a release tag. Offers a link to
the download page and a dismiss control; dismissing records the version in
settings so the banner stays quiet until something newer still ships.
"""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from app.core.settings import load_settings, save_settings
from app.core.update_check import RELEASES_PAGE
from app.i18n import tr

_BANNER_STYLE = (
    "background-color: #2f855a; color: white; padding: 6px 12px;"
)
_LINK_STYLE = (
    "QPushButton { color: white; text-decoration: underline; font-weight: 600; "
    "border: none; background: transparent; padding: 0 6px; }"
)
_CLOSE_STYLE = (
    "QPushButton { color: white; font-weight: 700; border: none; "
    "background: transparent; padding: 0 6px; }"
)


class UpdateBanner(QWidget):
    """A slim green bar shown only when an update is available."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._version = ""

        self._label = QLabel()
        self._label.setWordWrap(True)

        self._download_btn = QPushButton()
        self._download_btn.setObjectName("updateDownloadButton")
        self._download_btn.setStyleSheet(_LINK_STYLE)
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.clicked.connect(self._on_download)

        self._close_btn = QPushButton("×")  # ×
        self._close_btn.setStyleSheet(_CLOSE_STYLE)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedWidth(28)
        self._close_btn.clicked.connect(self._on_dismiss)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label, stretch=1)
        layout.addWidget(self._download_btn)
        layout.addWidget(self._close_btn)

        self.setStyleSheet(_BANNER_STYLE)
        self.hide()

    def show_update(self, version: str) -> None:
        """Reveal the banner for ``version`` (a release tag such as ``v1.0.8``),
        unless the user already dismissed this or a newer version."""
        dismissed = load_settings().update_dismissed_version
        if dismissed and not _is_newer(version, dismissed):
            return
        self._version = version
        self._label.setText(tr("update_banner.available", version=_display(version)))
        self._download_btn.setText(tr("update_banner.download"))
        self._close_btn.setToolTip(tr("update_banner.dismiss"))
        self.show()

    def _on_download(self) -> None:
        QDesktopServices.openUrl(QUrl(RELEASES_PAGE))

    def _on_dismiss(self) -> None:
        settings = load_settings()
        settings.update_dismissed_version = self._version
        save_settings(settings)
        self.hide()


def _display(tag: str) -> str:
    """A tag as shown to the user: drop a leading v so "v1.0.8" reads "1.0.8"."""
    return tag.strip().lstrip("vV") or tag


def _is_newer(candidate: str, baseline: str) -> bool:
    from app.core.update_check import _parse_version

    try:
        return _parse_version(candidate) > _parse_version(baseline)
    except Exception:
        return False
