"""Entry point: python -m app.main"""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.config import ICON_PATH
from app.core.db import init_db
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def _fix_macos_activation_policy() -> None:
    """Override PyInstaller's LSBackgroundOnly=true before QApplication starts.

    PyInstaller's BUNDLE writes LSBackgroundOnly=true in the generated
    Info.plist.  macOS reads that at launch and assigns
    NSApplicationActivationPolicyProhibited, which prevents the process
    from ever becoming the foreground app — windows appear but keyboard
    events are routed to whatever app was previously active.

    The build scripts now fix the plist at build time, but this runtime
    override acts as belt-and-suspenders in case the plist fix is missed.
    """
    if sys.platform != "darwin":
        return
    try:
        import objc  # type: ignore[import-untyped]

        NSApp = objc.lookUpClass("NSApplication").sharedApplication()
        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
    except Exception:
        pass


def main() -> int:
    _fix_macos_activation_policy()
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
    # macOS Tahoe/Golden Gate silently drops window activation on launch.
    # Retry at increasing intervals to cover slow OS-level activation.
    for delay in (200, 600, 1500):
        QTimer.singleShot(delay, window.ensure_active)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
