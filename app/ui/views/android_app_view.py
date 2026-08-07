"""Direct Android APK download for the Easy-Post Mobile Companion.

Offered while the Google Play listing is still pending organisation-account
verification. Shown only on direct-download builds (never the Microsoft Store or
Mac App Store builds, which forbid linking to off-store app downloads) and only
to production-licence holders, since the companion pairs with the production
account. Both conditions are enforced at the nav level in main_window.py; the
view re-checks the licence defensively in refresh().
"""

import webbrowser

from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import ANDROID_APK_SHA256, ANDROID_APK_URL
from app.core.license import production_allowed
from app.i18n import tr
from app.ui.theme import TEXT_MUTED


class AndroidAppView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('android_app.title')}</h2>"))

        intro = QLabel(tr("android_app.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        play_soon = QLabel(tr("android_app.play_soon"))
        play_soon.setWordWrap(True)
        play_soon.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(play_soon)

        # Shown when the page is reached without a production licence (a nav-level
        # guard normally hides the page entirely, so this is a fallback).
        self._gate_label = QLabel()
        self._gate_label.setWordWrap(True)
        self._gate_label.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(self._gate_label)

        self._download_btn = QPushButton(tr("android_app.download_button"))
        self._download_btn.clicked.connect(self._on_download_clicked)
        layout.addWidget(self._download_btn)

        steps_group = QGroupBox(tr("android_app.steps_group"))
        steps_layout = QVBoxLayout(steps_group)
        steps = QLabel(tr("android_app.steps"))
        steps.setWordWrap(True)
        steps_layout.addWidget(steps)
        layout.addWidget(steps_group)

        # The checksum row lets a sideloaded download be verified before
        # installing. Hidden until a checksum is configured for the release.
        self._checksum_group = QGroupBox(tr("android_app.checksum_group"))
        checksum_layout = QVBoxLayout(self._checksum_group)
        checksum_note = QLabel(tr("android_app.checksum_note"))
        checksum_note.setWordWrap(True)
        checksum_note.setStyleSheet(f"color: {TEXT_MUTED};")
        checksum_layout.addWidget(checksum_note)

        checksum_row = QHBoxLayout()
        self._checksum_field = QLineEdit(ANDROID_APK_SHA256)
        self._checksum_field.setReadOnly(True)
        checksum_row.addWidget(self._checksum_field, stretch=1)
        self._copy_checksum_btn = QPushButton(tr("android_app.copy_checksum"))
        self._copy_checksum_btn.clicked.connect(self._on_copy_checksum)
        checksum_row.addWidget(self._copy_checksum_btn)
        checksum_layout.addLayout(checksum_row)
        layout.addWidget(self._checksum_group)
        self._checksum_group.setVisible(bool(ANDROID_APK_SHA256))

        reinstall = QLabel(tr("android_app.reinstall_note"))
        reinstall.setWordWrap(True)
        reinstall.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(reinstall)

        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        """Re-check eligibility whenever the page is shown. The nav guard should
        keep this page hidden without a production licence, but re-checking means
        the download is never offered to an unlicensed session even if reached."""
        allowed = production_allowed()
        self._download_btn.setEnabled(allowed)
        if allowed:
            self._gate_label.setVisible(False)
        else:
            self._gate_label.setText(tr("android_app.needs_production"))
            self._gate_label.setVisible(True)

    def _on_download_clicked(self) -> None:
        webbrowser.open(ANDROID_APK_URL)

    def _on_copy_checksum(self) -> None:
        QApplication.clipboard().setText(ANDROID_APK_SHA256)
        self._copy_checksum_btn.setText(tr("android_app.copied"))
