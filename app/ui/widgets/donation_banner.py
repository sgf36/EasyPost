"""Dismissible banner inviting an optional donation via Stripe."""

import webbrowser

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from app.config import DONATION_URL
from app.core.settings import load_settings, save_settings
from app.i18n import tr

_STYLE = "background-color: #f6e05e; color: #1a202c; padding: 4px 12px;"


class DonationBanner(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_STYLE)

        label = QLabel(tr("donation_banner.message"))

        support_btn = QPushButton(tr("donation_banner.support_button"))
        support_btn.clicked.connect(lambda: webbrowser.open(DONATION_URL))

        dismiss_btn = QPushButton("×")
        dismiss_btn.setFixedWidth(28)
        dismiss_btn.clicked.connect(self._on_dismiss)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label, stretch=1)
        layout.addWidget(support_btn)
        layout.addWidget(dismiss_btn)

        self.setVisible(not load_settings().donation_banner_dismissed)

    def _on_dismiss(self) -> None:
        settings = load_settings()
        settings.donation_banner_dismissed = True
        save_settings(settings)
        self.setVisible(False)
