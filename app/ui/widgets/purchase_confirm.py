"""Extra confirmation gate for any action that spends real money."""

from PySide6.QtWidgets import QMessageBox, QWidget

from app.core.client import client_manager
from app.i18n import tr


def confirm_if_production(parent: QWidget, description: str) -> bool:
    """Returns True if the action should proceed. In test mode, always True.
    In production mode, requires an explicit Yes on a warning dialog.
    """
    if not client_manager.is_production():
        return True

    result = QMessageBox.warning(
        parent,
        tr("purchase_confirm.warning_title"),
        tr("purchase_confirm.warning_body", description=description),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return result == QMessageBox.StandardButton.Yes
