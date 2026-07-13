"""Entry point: python -m app.main"""

import sys

from PySide6.QtWidgets import QApplication

from app.core.db import init_db
from app.ui.main_window import MainWindow


def main() -> int:
    init_db()
    app = QApplication(sys.argv)
    app.setApplicationName("EasyPost Desktop")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
