"""Capture Microsoft Store screenshots of every nav view, in several languages.

Driven through Qt rather than a mouse: the window is built, a view is selected,
the event loop is pumped until it settles, and the widget is grabbed. That makes
the run deterministic and repeatable, which matters when the same set has to be
produced five times over.

The locale has to be set *before* MainWindow is constructed, because tr() is
resolved as each widget is built rather than on every paint.
"""
import sys
import time
from pathlib import Path

APP = Path(r"C:/Users/SpencerFields/OneDrive - Spencer Fields/Apps/Claude/EasyPost-Desktop-App")
sys.path.insert(0, str(APP))

OUT = Path(r"C:/Users/SpencerFields/OneDrive - Spencer Fields/Apps/Claude/EasyPost-Desktop-App/store_assets/screenshots")

# Microsoft Store wants at least 1366x768. 1600x1000 matches the earlier set.
SIZE = (1600, 1000)

# Top five languages by total speakers, English included as required.
LANGS = [
    ("en", "en-us", "English"),
    ("zh", "zh-hans", "Chinese (Simplified)"),
    ("hi", "hi-in", "Hindi"),
    ("es", "es-es", "Spanish"),
    ("fr", "fr-fr", "French"),
]

# Views worth showing, in listing order. Connect AI Agents is deliberately
# excluded: the Store build cannot run the MCP server, so advertising it would
# misrepresent the package being submitted.
SHOTS = [
    ("01_dashboard",      "_dashboard_view",       None),
    ("02_create_shipment", "_create_shipment_view", "refresh_address_choices"),
    ("03_address_book",   "_address_book_view",    "refresh_table"),
    ("04_tracking",       "_tracking_view",        "refresh_table"),
    ("05_history",        "_history_view",         "refresh_table"),
    ("06_batch",          "_batch_view",           "refresh_address_choices"),
    ("07_reports",        "_reports_view",         "refresh"),
    ("08_hts_lookup",     "_hts_lookup_view",      None),
    ("09_settings",       "_settings_view",        "refresh"),
]


def settle(app, ms=700):
    """Let layout, styling and any queued signals finish before grabbing."""
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main():
    from app.core.settings import load_settings, save_settings

    target = sys.argv[1]
    settings = load_settings()
    settings.locale = target
    save_settings(settings)

    # Import only after the locale is persisted, so catalogues load correctly.
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    from app.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    try:
        apply_theme(app)
    except Exception:
        pass

    win = MainWindow()
    win.resize(*SIZE)
    win.show()
    # Skip the gates: we want the shell, not the setup wizard.
    win._show_app_shell()
    settle(app, 1200)

    lang_dir = OUT / target
    lang_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for label, attr, refresh in SHOTS:
        view = getattr(win, attr, None)
        if view is None:
            print(f"    skip {label}: no {attr}")
            continue
        # Drive the sidebar rather than the stack, so the highlighted nav row
        # matches the page on screen. Setting the stack directly leaves the
        # selection stranded on whatever was chosen last.
        stack_index = win._view_stack.indexOf(view)
        row = next(
            (r for r in range(win._nav.count())
             if win._nav.item(r).data(Qt.ItemDataRole.UserRole) == stack_index),
            None,
        )
        if row is None:
            print(f"    skip {label}: no nav row")
            continue
        win._nav.setCurrentRow(row)
        if refresh:
            fn = getattr(view, refresh, None)
            if callable(fn):
                try:
                    fn()
                except Exception as exc:
                    print(f"    note {label}: {refresh}() -> {str(exc)[:60]}")
        settle(app, 800)

        # The rates table is the whole point of this screen, so actually shop
        # rates rather than photographing an empty grid. Real test-mode carrier
        # responses; nothing is purchased.
        if label.startswith("02"):
            try:
                if view._from_combo.count() > 1 and view._to_combo.count() > 1:
                    view._from_combo.setCurrentIndex(1)
                    view._to_combo.setCurrentIndex(2 if view._to_combo.count() > 2 else 0)
                    settle(app, 300)
                    view._on_get_rates_clicked()
                    # Wait for the async carrier round-trip to land.
                    for _ in range(40):
                        settle(app, 500)
                        if view._rates_table.rowCount() > 0:
                            break
                    print(f"    rates rows: {view._rates_table.rowCount()}")
            except Exception as exc:
                print(f"    note rates: {str(exc)[:80]}")
            settle(app, 600)

        path = lang_dir / f"{label}.png"
        pix = win.grab()
        pix.save(str(path), "PNG")
        written.append(path.name)

    print(f"  {target}: {len(written)} shots -> {lang_dir}")
    win.close()
    app.quit()


if __name__ == "__main__":
    main()
