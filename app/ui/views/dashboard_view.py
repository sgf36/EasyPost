"""Landing view shown after setup — placeholder until Stage 3+ views land."""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.i18n import tr


class DashboardView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("dashboard.title")))
        layout.addWidget(
            QLabel(tr("dashboard.placeholder_text"))
        )
        layout.addStretch(1)
