"""Entry point: python -m app.main"""

import sys

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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
