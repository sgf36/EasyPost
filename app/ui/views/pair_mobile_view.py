"""Pair the Easy-Post Mobile Companion with this desktop.

Shows a QR code the phone scans to pair. The code carries only a one-time token,
never the production key (see app/services/mobile_pairing.py). Gated on the
production version, since the companion drives the production EasyPost account.
"""

import io

import segno
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.license import production_allowed
from app.i18n import tr
from app.services.mobile_pairing import PairingError, production_key, register_pairing
from app.ui.theme import TEXT_MUTED
from app.ui.widgets.async_worker import run_async


def _qr_pixmap(payload: str) -> QPixmap:
    """Render a scannable QR of `payload` to a Qt pixmap via segno (pure Python)."""
    buffer = io.BytesIO()
    segno.make(payload, error="m").save(buffer, kind="png", scale=6, border=2)
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return pixmap


class PairMobileView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('pair_mobile.title')}</h2>"))

        intro = QLabel(tr("pair_mobile.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Shown when pairing is unavailable (no production licence / no key).
        self._gate_label = QLabel()
        self._gate_label.setWordWrap(True)
        self._gate_label.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(self._gate_label)

        self._generate_btn = QPushButton(tr("pair_mobile.generate_button"))
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        layout.addWidget(self._generate_btn)

        # The QR and its instructions, hidden until a code is generated.
        self._qr_group = QGroupBox(tr("pair_mobile.qr_group"))
        qr_layout = QVBoxLayout(self._qr_group)
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self._qr_label)

        self._expiry_label = QLabel(tr("pair_mobile.expiry_note"))
        self._expiry_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._expiry_label.setStyleSheet(f"color: {TEXT_MUTED};")
        qr_layout.addWidget(self._expiry_label)

        steps = QLabel(tr("pair_mobile.steps"))
        steps.setWordWrap(True)
        qr_layout.addWidget(steps)

        self._regenerate_btn = QPushButton(tr("pair_mobile.regenerate_button"))
        self._regenerate_btn.clicked.connect(self._on_generate_clicked)
        qr_layout.addWidget(self._regenerate_btn)

        layout.addWidget(self._qr_group)

        security = QLabel(tr("pair_mobile.security_note"))
        security.setWordWrap(True)
        security.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(security)

        layout.addStretch(1)
        self._qr_group.setVisible(False)
        self.refresh()

    def refresh(self) -> None:
        """Re-evaluate whether pairing is available and reflect it in the UI.
        Called whenever the page is shown (licence/key state may have changed)."""
        if not production_allowed():
            self._set_unavailable(tr("pair_mobile.needs_production"))
        elif not production_key():
            self._set_unavailable(tr("pair_mobile.needs_key"))
        else:
            self._gate_label.setVisible(False)
            self._generate_btn.setEnabled(True)

    def _set_unavailable(self, message: str) -> None:
        self._gate_label.setText(message)
        self._gate_label.setVisible(True)
        self._generate_btn.setEnabled(False)
        self._qr_group.setVisible(False)

    def _on_generate_clicked(self) -> None:
        self._generate_btn.setEnabled(False)
        self._generate_btn.setText(tr("pair_mobile.generating_button"))
        self._regenerate_btn.setEnabled(False)
        self._pending_task = run_async(register_pairing, self)
        self._pending_task.succeeded.connect(self._on_registered)
        self._pending_task.failed.connect(self._on_register_failed)

    def _on_registered(self, result: dict) -> None:
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText(tr("pair_mobile.generate_button"))
        self._regenerate_btn.setEnabled(True)
        self._qr_label.setPixmap(_qr_pixmap(result["qr_payload"]))
        self._qr_group.setVisible(True)

    def _on_register_failed(self, exc: Exception) -> None:
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText(tr("pair_mobile.generate_button"))
        self._regenerate_btn.setEnabled(True)
        known = {"no_production_key", "no_license", "invalid_license", "network", "server"}
        reason = exc.reason if isinstance(exc, PairingError) and exc.reason in known else "server"
        self._set_unavailable(tr(f"pair_mobile.error_{reason}"))
