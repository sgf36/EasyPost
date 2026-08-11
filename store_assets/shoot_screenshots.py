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


def _sample_label(n):
    """A representative 4x6 shipping label (PNG bytes) for the print-sheet shot.

    The deterministic harness must not hit the network, so the print-sheet
    dialog's fetch is stubbed with these — a plain carrier-style label with a
    barcode and tracking line, so the preview looks real without being any
    genuine customer's data.
    """
    import hashlib
    import io

    from PIL import Image, ImageDraw

    w, h = 800, 1200
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, w - 5, h - 5], outline="black", width=4)
    d.rectangle([0, 0, w, 120], fill="black")
    d.text((30, 44), f"EASY-POST  ·  {n}", fill="white")
    d.text((30, 170), "USPS Priority Mail", fill="black")
    d.text((30, 214), "To: Sample Recipient", fill="black")
    x = 40
    seed = hashlib.md5(str(n).encode()).digest()
    for i in range(120):
        bw = 3 + (seed[i % len(seed)] % 6)
        if i % 2 == 0:
            d.rectangle([x, h - 260, x + bw, h - 80], fill="black")
        x += bw + 3
        if x > w - 60:
            break
    d.text((40, h - 60), f"9400 1000 0000 000{n}", fill="black")
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


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
    # Render with the real platform plugin (correct fonts) but keep the window
    # off-screen: WA_DontShowOnScreen lays out and paints the widget without it
    # ever appearing on the display. Do NOT run this under QT_QPA_PLATFORM=
    # offscreen — that plugin renders every glyph as a tofu box on Windows.
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
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
        # Each view is wrapped in a QScrollArea before being added to the stack
        # (see MainWindow._build_nav), so the view is not a direct stack child
        # and indexOf(view) returns -1. Find the scroller that holds it.
        stack_index = win._view_stack.indexOf(view)
        if stack_index == -1:
            from PySide6.QtWidgets import QScrollArea

            for i in range(win._view_stack.count()):
                w = win._view_stack.widget(i)
                if isinstance(w, QScrollArea) and w.widget() is view:
                    stack_index = i
                    break
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

        # HTS Lookup is a reference tool, so an empty results grid says nothing.
        # Run a real search (live USITC data) so the screenshot shows codes and
        # duty rates. Nothing is purchased or changed.
        if label.startswith("08"):
            try:
                view._search_input.setText("copper")
                view._on_search_clicked()
                for _ in range(40):
                    settle(app, 500)
                    if view._table.rowCount() > 0:
                        break
                print(f"    hts rows: {view._table.rowCount()}")
            except Exception as exc:
                print(f"    note hts: {str(exc)[:80]}")
            settle(app, 600)

        path = lang_dir / f"{label}.png"
        pix = win.grab()
        pix.save(str(path), "PNG")
        written.append(path.name)

    # 10 — Print sheet dialog (new in 1.1.2). Stub the network fetch with sample
    # labels so the harness stays offline, then composite the dialog onto a
    # Store-sized canvas (the dialog alone is below the 1366x768 minimum).
    try:
        from app.ui.widgets import print_sheet_dialog as psd

        psd.fetch_label_images = lambda urls: ([_sample_label(k) for k in range(1, 5)], [])
        dlg = psd.PrintSheetDialog(["a", "b", "c", "d"], win)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dlg.show()
        for _ in range(40):
            settle(app, 200)
            pm = dlg._preview.pixmap()
            if dlg._save_btn.isEnabled() and pm is not None and not pm.isNull():
                break
        settle(app, 400)
        tmp = lang_dir / "_dlg.png"
        dlg.grab().save(str(tmp), "PNG")
        dlg.close()

        from PIL import Image

        bg = Image.new("RGB", SIZE, (245, 246, 248))
        fg = Image.open(str(tmp)).convert("RGB")
        bg.paste(fg, ((SIZE[0] - fg.width) // 2, (SIZE[1] - fg.height) // 2))
        out = lang_dir / "10_print_sheet.png"
        bg.save(str(out), "PNG")
        tmp.unlink(missing_ok=True)
        written.append(out.name)
        print(f"    print_sheet dialog {fg.width}x{fg.height}")
    except Exception as exc:
        print(f"    note print_sheet: {str(exc)[:120]}")

    print(f"  {target}: {len(written)} shots -> {lang_dir}")
    win.close()
    app.quit()


if __name__ == "__main__":
    main()
