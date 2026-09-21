"""Entry point: python -m app.main"""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.config import ICON_PATH
from app.core.db import init_db
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def main() -> int:
    init_db()
    app = QApplication(sys.argv)
    app.setApplicationName("EasyPost Desktop")
    apply_theme(app)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    window.activateWindow()
    window.raise_()
    # macOS Tahoe silently drops window activation on launch due to
    # anti-focus-stealing measures.  Re-activate after the event loop
    # has processed the initial paint so the window becomes key.
    QTimer.singleShot(200, window.ensure_active)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
