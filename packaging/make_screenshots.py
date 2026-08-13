"""Render store screenshots without a human, a display, or a rented Mac.

Qt can paint a widget straight into a pixmap with ``QWidget.grab()``, which
needs no window server at all under the ``offscreen`` platform plugin. So the
whole screenshot set can be produced by CI: run this on a `macos-latest` runner
and the images carry genuine macOS fonts and control metrics; run it on
`windows-latest` for the Microsoft Store and the website. One script, every
channel, identical output every time.

That removes the only part of the macOS release that still wanted an
interactive Mac — packaging, signing, notarisation and the App Store upload are
already automated in .github/workflows/build.yml.

Two deliberate choices:

* **Seeded data, not the user's.** Every run points at a throwaway database
  filled with the same invented shipments, so screenshots are reproducible and
  no real address, tracking number or customer name is ever published. Store
  screenshots are public; the live database is not a safe source for them.
* **Rendered at scale, not upscaled.** Qt is asked to paint at the target
  device pixel ratio rather than a small image being stretched, so a 2880x1800
  Retina asset is genuinely sharp.

Usage:
    python packaging/make_screenshots.py --platform mac --out dist/screenshots
    python packaging/make_screenshots.py --platform windows --locale fr
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Rendering happens through the NATIVE platform plugin, and no window is ever
# shown — grab() paints a widget that was never mapped to the screen. That
# matters: the `offscreen` plugin ships no font database, so every string comes
# out as tofu boxes. Verified by looking at the output rather than trusting the
# exit code. `--offscreen` is kept for a headless machine with no window
# station at all, where unreadable images still beat no images.
#
# HiDPI scaling is pinned off so a 1366x768 request yields exactly 1366x768
# rather than being silently multiplied by the display's scale factor (this
# machine returned 1708x960 before it was pinned). Retina assets are then
# produced deliberately, by the scale column in TARGETS.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Store-required canvas sizes, in logical pixels, with the scale factor each is
# rendered at. Apple and Microsoft both police these dimensions, so they are
# produced exactly rather than resized afterwards.
TARGETS = {
    "mac": [
        ("1280x800", 1280, 800, 1),
        ("1440x900", 1440, 900, 1),
        ("2560x1600", 1280, 800, 2),
        ("2880x1800", 1440, 900, 2),
    ],
    "windows": [
        ("1366x768", 1366, 768, 1),
        ("2160x1440", 1080, 720, 2),
    ],
    # The size the Microsoft Store listing sources in store_assets/screenshots
    # are captured at. A replacement has to match its siblings exactly or it
    # sits visibly different in the carousel beside the eight it did not
    # replace, and build_listing_import.py copies these through without
    # resizing.
    "store": [
        ("1600x1000", 1600, 1000, 1),
    ],
}


def _seed_database(db_path: Path) -> None:
    """Fill a throwaway database with invented, obviously-fake data.

    Nothing here is real. Screenshots end up on public store listings, so
    seeding from the live database would publish genuine customer addresses and
    tracking numbers.
    """
    from app.core.db import db_cursor, init_db

    init_db()

    # Seeded under whichever mode the app considers active. With no credentials
    # stored that is "test", but a developer running this on their own machine
    # may well be in production — and an empty table makes a poor screenshot.
    from app.core.client import client_manager

    MODE = client_manager.active_mode

    with db_cursor() as cur:
        cur.executemany(
            "INSERT OR REPLACE INTO addresses (id, mode, label, name, company, street1,"
            " city, state, zip, country, verified, is_favorite)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("adr_demo1", MODE, "Office", "Alex Morgan", "Northwind Trading",
                 "24 Example Street", "London", "", "SW1A 1AA", "GB", 1, 1),
                ("adr_demo2", MODE, "Warehouse", "Sam Rivera", "Northwind Logistics",
                 "8 Sample Way", "Manchester", "", "M1 2AB", "GB", 1, 0),
            ],
        )
        cur.executemany(
            "INSERT OR REPLACE INTO shipments (id, mode, status, to_address, from_address,"
            " carrier, service, rate_amount, rate_currency, tracking_code,"
            " insured_amount, refund_status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            # Two shipments made a thin History table and, once spend stopped
            # being summed across currencies, a one-bar chart. Most of these are
            # GBP so the chart has something to compare; the two USD rows keep
            # the multi-currency total honest and on screen, which is the whole
            # point of reporting per currency.
            # The last two columns are Insured and Refund status. Left null they
            # rendered as an em dash on all eight rows, so two of History's nine
            # columns advertised nothing at all. Three insured parcels and two
            # refunds in some state is what those columns look like in use.
            [
                ("shp_demo1", MODE, "purchased", "Alex Morgan, London",
                 "Northwind Trading", "RoyalMailV3", "RoyalMail2ndClassSignedFor",
                 "3.85", "GBP", "AA000000001GB", "50.00", None),
                ("shp_demo2", MODE, "purchased", "Sam Rivera, Manchester",
                 "Northwind Trading", "USPS", "Priority", "8.40", "USD",
                 "EZ1000000001", None, None),
                ("shp_demo3", MODE, "purchased", "Priya Nair, Bristol",
                 "Northwind Trading", "RoyalMailV3", "RoyalMailTracked24",
                 "5.95", "GBP", "AA000000002GB", "120.00", None),
                ("shp_demo4", MODE, "purchased", "Jonas Weber, Leeds",
                 "Northwind Trading", "Evri", "Next Day", "3.09", "GBP",
                 "EZ1000000002", None, "submitted"),
                ("shp_demo5", MODE, "purchased", "Chloe Dupont, Glasgow",
                 "Northwind Trading", "Evri", "Standard", "2.89", "GBP",
                 "EZ1000000003", None, None),
                ("shp_demo6", MODE, "purchased", "Marco Rossi, Cardiff",
                 "Northwind Trading", "DHLExpress", "ExpressWorldwide", "24.60",
                 "GBP", "EZ1000000004", "250.00", None),
                ("shp_demo7", MODE, "refunded", "Yuki Tanaka, Belfast",
                 "Northwind Trading", "FedEx", "FEDEX_GROUND", "11.20", "USD",
                 "EZ1000000005", None, "refunded"),
                ("shp_demo8", MODE, "purchased", "Ana Silva, Sheffield",
                 "Northwind Trading", "RoyalMailV3", "RoyalMail1stClass", "4.45",
                 "GBP", "AA000000003GB", None, None),
            ],
        )
        # The carrier catalogue, so the batch service picker renders populated.
        # Credentials are stubbed, so there is no live fetch to fall back on —
        # without this the picker would screenshot as "No services available".
        cur.execute("DELETE FROM carriers_cache")
        cur.executemany(
            "INSERT INTO carriers_cache (name, human_readable) VALUES (?, ?)",
            [("royalmailv3", "RoyalMailV3"), ("usps", "USPS"), ("fedex", "FedEx"),
             ("dhlexpress", "DHL Express"), ("evri", "Evri")],
        )
        cur.execute("DELETE FROM service_levels_cache")
        cur.executemany(
            "INSERT INTO service_levels_cache (carrier, name, human_readable,"
            " dimensions, max_weight) VALUES (?,?,?,?,?)",
            [
                ("royalmailv3", "RoyalMail1stClass", "RoyalMail1stClass", "", None),
                ("royalmailv3", "RoyalMail2ndClass", "RoyalMail2ndClass", "", None),
                ("royalmailv3", "RoyalMail1stClassSignedFor",
                 "RoyalMail1stClassSignedFor", "", None),
                ("royalmailv3", "RoyalMail2ndClassSignedFor",
                 "RoyalMail2ndClassSignedFor", "", None),
                ("royalmailv3", "RoyalMailTracked24", "RoyalMailTracked24", "", None),
                ("usps", "First", "First", "", None),
                ("usps", "Priority", "Priority", "", None),
                ("usps", "GroundAdvantage", "GroundAdvantage", "", None),
                ("fedex", "FEDEX_GROUND", "FEDEX_GROUND", "", None),
                ("dhlexpress", "ExpressWorldwide", "ExpressWorldwide", "", None),
                ("evri", "Next Day", "Next Day", "", None),
            ],
        )
        # Scheduled pickups, so the Pickups page has something in its lower
        # table. The page took the dashboard's listing slot, and it is the only
        # screenshot whose bottom half is otherwise two empty grids.
        cur.executemany(
            "INSERT OR REPLACE INTO pickups (id, mode, status, address,"
            " min_datetime, max_datetime, shipment_ids)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                ("pck_demo1", MODE, "scheduled", "Northwind Trading, London",
                 "2026-08-13 09:00", "2026-08-13 17:00", "shp_demo1"),
                ("pck_demo2", MODE, "scheduled", "Northwind Logistics, Manchester",
                 "2026-08-14 09:00", "2026-08-14 12:00", "shp_demo2"),
            ],
        )
        cur.executemany(
            "INSERT OR REPLACE INTO trackers (id, mode, tracking_code, carrier, status,"
            " status_detail, est_delivery_date, last_checked_at)"
            " VALUES (?,?,?,?,?,?,?,datetime('now'))",
            # Tracking is a screen about colour-coded state, so two rows both
            # reading the same thing said nothing about it. These span the
            # range EasyPost actually reports, including a failure with a
            # detail — the one case where the status alone is not enough.
            [
                ("trk_demo1", MODE, "AA000000001GB", "RoyalMailV3", "in_transit",
                 None, "2026-08-15"),
                ("trk_demo2", MODE, "EZ1000000001", "USPS", "delivered", None,
                 "2026-08-11"),
                ("trk_demo3", MODE, "AA000000002GB", "RoyalMailV3",
                 "out_for_delivery", None, "2026-08-13"),
                ("trk_demo4", MODE, "EZ1000000002", "Evri", "pre_transit", None,
                 "2026-08-18"),
                # Two rows carry a status_detail, which is the line that says
                # why. These are EasyPost's own vocabulary and are translated
                # into all fifty languages, so they no longer sit in English in
                # the middle of an otherwise Japanese table.
                ("trk_demo5", MODE, "EZ1000000004", "DHLExpress", "in_transit",
                 "weather_delay", "2026-08-14"),
                ("trk_demo6", MODE, "EZ1000000005", "FedEx", "failure",
                 "damaged", "2026-08-16"),
                ("trk_demo7", MODE, "AA000000003GB", "RoyalMailV3",
                 "return_to_sender", None, "2026-08-19"),
                ("trk_demo8", MODE, "EZ1000000003", "Evri",
                 "available_for_pickup", None, "2026-08-13"),
            ],
        )


def _stub_credentials() -> None:
    """Replace the credential store with a placeholder for the whole run.

    Screenshots are published publicly. Two things must never reach one: a real
    API key, and live data pulled from a real EasyPost account. The keyring is
    machine-wide and unaffected by the scratch data directory, so it is stubbed
    here rather than hoped about. The placeholder is deliberately not a valid
    key shape, so any accidental network call fails loudly instead of quietly
    succeeding against a real account.
    """
    from app.core import credential_store
    from app.core.client import client_manager

    placeholder = credential_store.Credentials(
        test_key="EZTK_screenshot_placeholder_not_a_real_key",
        production_key=None,
        active_mode="test",
    )
    credential_store.load_credentials = lambda: placeholder
    client_manager._credentials = placeholder


# Pages that must never appear in a store screenshot, whatever else changes.
# The first-run wizard renders API key fields; the AI-agent and pairing pages
# render pairing tokens. Naming them here means adding a page to the screenshot
# set can never quietly add one of these.
#
# settings_view was on this list until 1.2.1 and is deliberately no longer.
# It was listed because SettingsView.refresh() loaded the stored keys into its
# two fields on every visit, so the page genuinely could not be photographed.
# 1.2.1 removed that: the fields are never populated and a stored key shows
# only as a fixed-length mask. The page is now safe to capture, and the store
# listing has carried a Settings screenshot since 1.1.1 regardless — so keeping
# the ban meant the one image the tool refused to produce was one the listing
# still needed, and it had to come from somewhere else.
#
# This is the ban being retired because the underlying exposure was fixed, not
# because the screenshot was inconvenient. If SettingsView is ever changed to
# put a key on screen again, put it back.
#
# The names are matched against a view's module, so a name matching no module
# guards nothing. "pair_screen" was such a name: the mobile pairing page is
# pair_mobile_view, and it renders a QR carrying a one-time pairing token, so
# for as long as the entry read "pair_screen" that page was not covered at all.
# test_screenshot_guard.py now pins every entry to a module that exists.
FORBIDDEN_PAGES = {
    "setup_wizard", "connect_agents_view",
    "license_gate", "store_unlock", "pair_mobile_view",
}


def _settle(app, seconds: float = 2.5) -> None:
    """Pump the event loop so background page loads finish before the grab.

    Several pages fetch their catalogue on a worker thread. Grabbing straight
    after construction caught the batch picker mid-fetch, so the screenshot read
    "Loading the carrier catalogue..." with an empty carrier list.

    Deferred deletions are flushed too, and that is not a detail. processEvents()
    does not process DeferredDelete, and this tool never runs a real event loop,
    so anything discarded via deleteLater simply accumulates. QTableWidget
    replaces a cell widget that way: every refresh_scheduled() left its previous
    Cancel buttons parented to the viewport at (0, 0), and grab() painted them —
    putting a stray Cancel on top of the first address on the Pickups page. The
    app is not leaking; a running event loop reaps these immediately, so a user
    never sees them. It is only visible to a grab of a window that was never
    shown, which is exactly what this tool does.
    """
    import time

    from PySide6.QtCore import QEvent

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.05)


def _capture(widget, path: Path, width: int, height: int, scale: int) -> None:
    """Paint one widget to a PNG at an exact pixel size."""
    from PySide6.QtCore import QSize

    widget.resize(QSize(width, height))
    widget.setMinimumSize(QSize(width, height))
    # Let the layout settle before painting, or half the page renders unsized.
    widget.adjustSize()
    widget.resize(QSize(width, height))

    # Settle once more at the final size: cell widgets are positioned during
    # layout, and discarded ones are only reaped when deferred deletes are
    # flushed (see _settle). Both have to happen before the grab, not before
    # the resize.
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        _settle(app, 0.5)

    pixmap = widget.grab()
    if scale != 1:
        from PySide6.QtCore import Qt

        pixmap = pixmap.scaled(
            width * scale, height * scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not write {path}")


def _capture_window(app, view_class_name: str, path: Path,
                    width: int, height: int, scale: int) -> None:
    """Paint the whole application window with one page selected.

    Every image already on the store listings is a full window — mode banner,
    navigation sidebar and all. Capturing a bare view widget instead produces
    something that is recognisably the same product but visibly not the same
    screenshot, which looks wrong sitting beside the ones it did not replace.

    The page is chosen by driving the real navigation list rather than by
    setting the stack index directly, so whatever a page does when it is shown
    (`on_show`) runs exactly as it does for a user.
    """
    from app.ui.main_window import MainWindow
    from PySide6.QtCore import Qt

    window = MainWindow()

    # Force the application shell.
    #
    # MainWindow routes to the first-run wizard whenever it cannot find usable
    # credentials, and a CI runner never has any. Without this every capture is
    # the "Connect your EasyPost account" form — which is what the first macOS
    # run produced: twenty images, all of them the wizard, reported as success.
    window._show_app_shell()

    nav = window._nav
    stack = window._view_stack

    target_row = None
    for row in range(nav.count()):
        index = nav.item(row).data(Qt.ItemDataRole.UserRole)
        if index is None:  # section header
            continue
        holder = stack.widget(index)
        # Each page is wrapped in a QScrollArea, so the view is its child.
        view = holder.widget() if hasattr(holder, "widget") else holder
        if type(view).__name__ == view_class_name:
            target_row = row
            break
    if target_row is None:
        raise RuntimeError(f"No navigation entry renders {view_class_name}")

    # The forbidden-page check applied only to the hard-coded `pages` list, so
    # --window walked straight past it and could capture any page in the
    # navigation. Checked here, against the module of the view actually
    # selected, it covers both routes.
    module = type(view).__module__.rsplit(".", 1)[-1]
    if module in FORBIDDEN_PAGES:
        raise SystemExit(
            f"Refusing to screenshot {view_class_name}: {module} renders API "
            f"keys or pairing tokens, and screenshots are published publicly."
        )

    nav.setCurrentRow(target_row)
    _settle(app)
    holder = stack.widget(nav.item(target_row).data(Qt.ItemDataRole.UserRole))
    _pin_service_picker(holder, app)
    _seed_rate_table(holder)
    _seed_pickup_rates(holder)
    _seed_batch_preview(holder)
    _settle(app)

    _assert_no_orphan_cell_widgets(window, view_class_name)

    # Verified immediately before painting, not assumed. A screenshot run that
    # quietly captures the wrong screen still reports success, and the only
    # thing that catches it is someone opening the PNG.
    showing = window._root_stack.currentWidget()
    if showing is not window._app_shell:
        raise RuntimeError(
            f"Refusing to capture {view_class_name}: the window is showing "
            f"{type(showing).__name__}, not the application shell."
        )

    _capture(window, path, width, height, scale)
    return window


# The print sheet is a dialog, not a navigation page, so --window cannot reach
# it through the nav list like every other capture. It is requested by the same
# flag and dispatched here instead.
PRINT_SHEET = "PrintSheetDialog"


def _sample_label(n: int) -> bytes:
    """A representative 4x6 shipping label, as PNG bytes.

    The print sheet fetches its label images over the network, which this
    harness must never do, so the fetch is stubbed with these. Carrier-style
    enough that the preview reads as real, and invented so that no genuine
    customer's label is published.
    """
    import hashlib
    import io

    from PIL import Image, ImageDraw

    w, h = 800, 1200
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, w - 5, h - 5], outline="black", width=4)
    d.rectangle([0, 0, w, 120], fill="black")
    d.text((30, 44), f"EASY-POST  -  {n}", fill="white")
    d.text((30, 170), "USPS Priority Mail", fill="black")
    d.text((30, 214), "To: Sample Recipient", fill="black")
    x = 40
    seed = hashlib.md5(str(n).encode()).digest()
    for i in range(120):
        bar = 3 + (seed[i % len(seed)] % 6)
        if i % 2 == 0:
            d.rectangle([x, h - 260, x + bar, h - 80], fill="black")
        x += bar + 3
        if x > w - 60:
            break
    d.text((40, h - 60), f"9400 1000 0000 000{n}", fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _capture_print_sheet(app, path: Path, width: int, height: int, scale: int):
    """Paint the Export print sheet dialog onto a Store-sized canvas.

    The dialog is smaller than the minimum listing image, so it is centred on a
    canvas rather than stretched. It is shown with WA_DontShowOnScreen, which
    lays it out without ever putting it in front of anyone running this.

    The wait is on the dialog's own readiness — Save enabled and a preview
    pixmap present — not on a fixed sleep. Grabbing early produced the empty
    grey preview box, which is indistinguishable from a working screenshot
    unless you open it.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap

    from app.ui.main_window import MainWindow
    from app.ui.widgets import print_sheet_dialog as psd

    window = MainWindow()
    window._show_app_shell()

    psd.fetch_label_images = lambda urls: ([_sample_label(k) for k in range(1, 5)], [])
    dialog = psd.PrintSheetDialog(["a", "b", "c", "d"], window)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dialog.show()

    import time

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        _settle(app, 0.2)
        preview = dialog._preview.pixmap()
        if dialog._save_btn.isEnabled() and preview is not None and not preview.isNull():
            break
    else:
        raise RuntimeError(
            "print sheet preview never rendered; the grab would be an empty box"
        )
    _settle(app, 0.4)

    shot = dialog.grab()
    if shot.width() > width or shot.height() > height:
        shot = shot.scaled(
            width, height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    canvas = QPixmap(width, height)
    canvas.fill(QColor("#f5f6f8"))
    painter = QPainter(canvas)
    painter.drawPixmap((width - shot.width()) // 2, (height - shot.height()) // 2, shot)
    painter.end()

    if scale != 1:
        canvas = canvas.scaled(
            width * scale, height * scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not canvas.save(str(path), "PNG"):
        raise RuntimeError(f"Could not write {path}")
    dialog.close()
    return window


def _assert_no_orphan_cell_widgets(window, view_class_name: str) -> None:
    """Fail if a table holds a widget that is no longer one of its cells.

    Replacing a cell widget discards the old one with deleteLater, which only
    runs when deferred deletes are flushed. Unflushed, the discard stays
    parented to the viewport at (0, 0) and grab() paints it: the address book
    published three buttons — Favorite, Edit and Delete — stacked on top of the
    first row's data, in all seven languages, and the run reported success.

    _settle now flushes those, so this is the check that the fix is holding. It
    is cheap and it is the only thing standing between a stale widget and a
    published screenshot.
    """
    from PySide6.QtCore import QModelIndex
    from PySide6.QtWidgets import QAbstractItemView, QWidget

    for table in window.findChildren(QAbstractItemView):
        model = table.model()
        if model is None:
            continue
        registered = set()

        def collect(parent):
            """Recurse: the rates tree hangs its Buy buttons off child rows, so
            a top-level-only walk reports every one of them as an orphan."""
            try:
                rows = model.rowCount(parent)
                cols = model.columnCount(parent)
            except TypeError:
                # List models take no column argument.
                try:
                    rows, cols = model.rowCount(parent), 1
                except TypeError:
                    return
            for r in range(rows):
                for c in range(cols):
                    index = model.index(r, c, parent)
                    w = table.indexWidget(index)
                    if w is not None:
                        registered.add(id(w))
                    # hasChildren is private in PySide6; a child row count of
                    # zero means a leaf, and table models return zero here, so
                    # this terminates on both shapes.
                    if c == 0 and model.rowCount(index) > 0:
                        collect(index)

        collect(QModelIndex())
        orphans = [w for w in table.viewport().findChildren(QWidget)
                   if id(w) not in registered and w.parent() is table.viewport()]
        if orphans:
            raise RuntimeError(
                f"Refusing to capture {view_class_name}: "
                f"{type(table).__name__} holds {len(orphans)} widget(s) that are "
                f"no longer cells ({[w.__class__.__name__ for w in orphans][:4]}). "
                f"They paint over the first row."
            )


# Shown on the batch screenshot. A carrier and service a reader recognises,
# rather than whichever name happens to sort first.
SHOWCASE_CARRIER = "dhlexpress"
# Without this the service falls to whatever sorts first, which for DHL is
# "BreakBulkEconomy" — a real service, but freight jargon to a reader deciding
# whether this app posts their parcels.
SHOWCASE_SERVICE = "ExpressWorldwide"


def _pin_service_picker(holder, app=None) -> None:
    """Force the batch carrier/service picker to a fixed, recognisable choice.

    Left alone, the combo shows whatever wins a race: the seeded cache resolves
    first on one run and the full built-in catalogue on the next, so the same
    page screenshotted twice offered "DHL Express" once and "Accurate" the
    other time. Across a localised set that means every language advertising a
    different carrier, which reads as carelessness rather than variety.

    Pinning once after a fixed settle was not enough — it is the same race, just
    narrower. A seven-language run had the Spanish catalogue still loading when
    the pin ran, and that language alone published "Accurate / Route" while the
    other six showed DHL Express. So the list is now waited on, and a carrier
    that never arrives raises rather than printing a note: an inconsistent set
    is exactly the failure a note gets skimmed past.
    """
    view = holder.widget() if hasattr(holder, "widget") else holder
    picker = getattr(view, "_service_picker", None)
    if picker is None:
        return
    carriers = picker._carrier_combo

    def _find(combo, wanted, by_data):
        for index in range(combo.count()):
            value = combo.itemData(index) if by_data else combo.itemText(index)
            if value == wanted:
                return index
        return None

    def _wait_for(combo, wanted, by_data, seconds=20.0):
        import time

        deadline = time.monotonic() + seconds
        while True:
            index = _find(combo, wanted, by_data)
            if index is not None:
                return index
            if time.monotonic() >= deadline:
                return None
            if app is not None:
                app.processEvents()
            time.sleep(0.05)

    index = _wait_for(carriers, SHOWCASE_CARRIER, True)
    if index is None:
        raise RuntimeError(
            f"{SHOWCASE_CARRIER} never appeared in the carrier list; the batch "
            f"screenshot would show {carriers.currentText()!r} and not match "
            f"the other languages."
        )
    carriers.setCurrentIndex(index)

    services = picker._service_combo
    index = _wait_for(services, SHOWCASE_SERVICE, False)
    if index is None:
        raise RuntimeError(
            f"{SHOWCASE_SERVICE} never appeared for {SHOWCASE_CARRIER}; the "
            f"batch screenshot would show {services.currentText()!r} and not "
            f"match the other languages."
        )
    services.setCurrentIndex(index)


# Rates shown on the Create Shipment screenshot.
#
# The rates table is filled by a live EasyPost call and credentials are stubbed
# (_stub_credentials), so a seeded run paints it empty — a poor primary
# screenshot, and visibly worse than the one already published. These invented
# quotes are pushed through the view's own _on_rates_received, so the grouping,
# the cheapest and fastest badges and the "included" column are all built by
# exactly the code a real fetch would run, rather than by a second rendering
# path that could drift from it.
#
# Invented, like the rest of the seed: nothing here came from a real account and
# no network call is made. The route is the seeded London -> Manchester one, so
# the carriers and prices are the domestic British ones a reader would expect.
_SHOWCASE_RATES = [
    ("rate_demo1", "Evri", "Standard", "2.89", 3, False),
    ("rate_demo2", "Evri", "Next Day", "3.09", 1, False),
    ("rate_demo3", "RoyalMailV3", "RoyalMail2ndClass", "3.35", 3, False),
    ("rate_demo4", "RoyalMailV3", "RoyalMail1stClass", "4.45", 1, False),
    ("rate_demo5", "RoyalMailV3", "RoyalMailTracked24", "5.95", 1, False),
    ("rate_demo6", "RoyalMailV3", "RoyalMail1stClassSignedFor", "6.85", 1, False),
    ("rate_demo7", "FedEx", "FEDEX_GROUND", "11.20", 2, False),
    ("rate_demo8", "DHL Express", "ExpressWorldwide", "24.60", 1, True),
]


class _DemoRate:
    """Duck-typed stand-in for an EasyPost Rate.

    Every helper in create_shipment_view reads rates through getattr, so a
    plain attribute holder is enough and pulling in the real SDK object would
    add nothing.
    """

    def __init__(self, rate_id, carrier, service, amount, days, guaranteed):
        self.id = rate_id
        self.carrier = carrier
        self.service = service
        self.rate = amount
        self.currency = "GBP"
        self.delivery_days = days
        self.delivery_date_guaranteed = guaranteed


class _DemoShipment:
    def __init__(self, rates):
        self.rates = rates
        self.messages = []


def _seed_rate_table(holder) -> None:
    """Fill the Create Shipment rates table with the invented quotes above.

    Identified by _populate_rates_tree, not by _on_rates_received alone. The
    Batch page grew a rate preview of its own, and for a while its handler had
    the same name and a different signature — so this hook found it, called it
    with the wrong arguments, and every batch screenshot failed. One method name
    is not an identity.
    """
    view = holder.widget() if hasattr(holder, "widget") else holder
    if not (hasattr(view, "_on_rates_received") and hasattr(view, "_populate_rates_tree")):
        return

    # Both address combos default to the first saved address, so the page
    # screenshots as posting from an office to itself. Point the destination at
    # the other seeded address to make it the London -> Manchester journey the
    # rates below are quoted for.
    to_combo = getattr(view, "_to_combo", None)
    if to_combo is not None and to_combo.count() > 1:
        to_combo.setCurrentIndex(1)

    rates = [_DemoRate(*row) for row in _SHOWCASE_RATES]
    view._on_rates_received(_DemoShipment(rates))


# Pickup rates, for the same reason as _SHOWCASE_RATES: the table is filled by
# a live call the screenshot run cannot make. Pushed through the view's own
# _on_pickup_created so the Buy column and the empty-result path behave exactly
# as they do for a real request.
_SHOWCASE_PICKUP_RATES = [
    ("DHL Express", "ExpressWorldwide", "8.50"),
    ("Evri", "Next Day", "9.25"),
    ("FedEx", "FEDEX_GROUND", "12.00"),
]


class _DemoPickupRate:
    def __init__(self, carrier, service, amount):
        self.carrier = carrier
        self.service = service
        self.rate = amount
        self.currency = "GBP"


class _DemoPickup:
    def __init__(self, rates):
        self.pickup_rates = rates
        self.messages = []


def _seed_pickup_rates(holder) -> None:
    """Fill the Pickups rates table with the invented quotes above."""
    view = holder.widget() if hasattr(holder, "widget") else holder
    if not hasattr(view, "_on_pickup_created") or not hasattr(view, "_rates_table"):
        return
    rates = [_DemoPickupRate(*row) for row in _SHOWCASE_PICKUP_RATES]
    view._on_pickup_created(_DemoPickup(rates))


# The Batch page is filled by choosing a CSV from a file dialog, which a
# screenshot run cannot do — so every published batch image showed "No CSV
# loaded", an empty four-column table and a row of greyed-out buttons. It was
# the one slot that argued the feature did not work.
#
# Two of these five rows are international, which is deliberate: that is what
# makes the customs block appear, and international customs is the headline of
# 1.2.2. One row carries an error, because a preview that never shows a problem
# does not explain what "validate" is for.
# Four rows, not five: the preview table is a fixed height and a fifth row
# pushed the invalid one out of sight behind a scrollbar, so the summary
# claimed an error the image could not show.
_SHOWCASE_IMPORT_ROWS = [
    (2, "Alex Morgan", "Manchester", "GB", "12x9x4 / 16oz", []),
    (3, "Priya Nair", "New York", "US", "14x10x6 / 32oz", []),
    (4, "Jonas Weber", "Berlin", "DE", "12x9x4 / 20oz", []),
    # One invalid row, so the preview demonstrates what "validate" is for. Its
    # error is produced by the app's own translated message rather than a
    # literal — these used to be bare English, which put an English string in
    # the most conspicuous column of every localised capture.
    (5, "Chloe Dupont", "Lyon", "FR", "12x9x4 / 14oz", ["__missing_to_zip__"]),
]


class _DemoImportRow:
    """Duck-typed to match what parse_import returns, in the shape
    _render_preview and _has_international_rows actually read."""

    def __init__(self, line_number, name, city, country, parcel, errors):
        from app.services.batches import _row_error

        self.line_number = line_number
        # The sentinel is expanded through the app's own translated message, so
        # the capture shows exactly what a user in that language would read.
        self.errors = [
            _row_error("required", "to_zip") if e == "__missing_to_zip__" else e
            for e in errors
        ]
        self.is_valid = not errors
        self._parcel = parcel
        self.fields = {
            "to_name": name,
            "to_city": city,
            "to_country": country,
            # _render_preview builds the parcel summary from these; the demo
            # rows carry it pre-formatted, so hand back the pieces it expects.
            "length": parcel.split("x")[0] if parcel else "",
            "width": parcel.split("x")[1] if parcel else "",
            "height": parcel.split("x")[2].split(" /")[0] if parcel else "",
            "weight": parcel.split("/ ")[1].replace("oz", "") if parcel else "",
        }


def _seed_batch_preview(holder) -> None:
    """Load invented recipients into the Batch preview, customs block and all."""
    view = holder.widget() if hasattr(holder, "widget") else holder
    if not (hasattr(view, "_render_preview") and hasattr(view, "_parsed_rows")):
        return
    view._parsed_rows = [_DemoImportRow(*row) for row in _SHOWCASE_IMPORT_ROWS]
    customs = getattr(view, "_customs_group", None)
    if customs is not None and hasattr(view, "_has_international_rows"):
        customs.setVisible(view._has_international_rows())
    view._render_preview()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=sorted(TARGETS), default="mac")
    parser.add_argument("--locale", default="en", help="UI language to render in")
    parser.add_argument("--out", default="dist/screenshots")
    parser.add_argument(
        "--scale-factor", type=int, default=1,
        help="Extra device pixel ratio, for Retina assets",
    )
    parser.add_argument(
        "--window", metavar="VIEWCLASS", action="append", default=[],
        help="Capture the whole application window with this view selected "
             "(e.g. BatchView), matching the framing of the published store "
             "screenshots. Repeatable. Suppresses the bare-view captures.",
    )
    parser.add_argument(
        "--offscreen", action="store_true",
        help="Force the offscreen Qt plugin. Last resort only — it has no font "
             "database, so every string renders as an empty box.",
    )
    args = parser.parse_args()
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    # A scratch app-data directory, so a developer running this locally never
    # has their real database or settings touched.
    scratch = Path(tempfile.mkdtemp(prefix="easypost-shots-"))
    os.environ["EASYPOST_DESKTOP_DATA_DIR"] = str(scratch)

    # The scratch directory does NOT isolate credentials: those live in the OS
    # keyring, which is machine-wide. Without this, a run on a developer's own
    # machine would load their real EasyPost API keys — and anything that
    # renders a key, or fetches live data belonging to a real account, would be
    # published to a public store listing. So the credential store is replaced
    # outright with an obvious placeholder before any app module can read it.
    _stub_credentials()

    from app.config import DATABASE_PATH  # noqa: F401  (honours the env var)
    from app.core.settings import load_settings, save_settings

    settings = load_settings()
    settings.locale = args.locale
    save_settings(settings)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    print(f"Qt platform plugin: {app.platformName()}")
    _seed_database(Path(str(DATABASE_PATH)))

    # i18n reads the active language from settings (current_locale), which the
    # write above has already set; clearing the cache makes it take effect.
    from app.i18n import clear_cache

    clear_cache()

    from app.ui.views.batch_view import BatchView
    from app.ui.views.create_shipment_view import CreateShipmentView
    from app.ui.views.history_view import HistoryView
    from app.ui.views.hts_lookup_view import HtsLookupView
    from app.ui.views.tracking_view import TrackingView

    pages = [
        ("01-create-shipment", CreateShipmentView),
        ("02-batch", BatchView),
        ("03-tracking", TrackingView),
        ("04-history", HistoryView),
        ("05-hts-lookup", HtsLookupView),
    ]

    # Enforced, not merely intended: a page whose module is on the forbidden
    # list can never be screenshotted, however the list above is edited later.
    for name, factory in pages:
        module = factory.__module__.rsplit(".", 1)[-1]
        if module in FORBIDDEN_PAGES:
            raise SystemExit(
                f"Refusing to screenshot {name}: {module} renders API keys or "
                f"pairing tokens, and screenshots are published publicly."
            )

    out_root = Path(args.out) / args.platform / args.locale
    written = 0
    # Held for the lifetime of the run. Destroying a page while its background
    # catalogue fetch is still in flight made the worker emit onto a dead
    # object: "RuntimeError: Signal source has been deleted".
    alive = []
    for label, width, height, scale in TARGETS[args.platform]:
        if args.window:
            for view_class in args.window:
                path = out_root / label / f"window-{view_class}.png"
                try:
                    if view_class == PRINT_SHEET:
                        alive.append(_capture_print_sheet(
                            app, path, width, height, scale * args.scale_factor))
                    else:
                        alive.append(_capture_window(
                            app, view_class, path, width, height,
                            scale * args.scale_factor))
                    written += 1
                except Exception as exc:  # noqa: BLE001 - report, do not abort
                    print(f"  FAILED {path}: {type(exc).__name__}: {exc}")
            app.processEvents()
            continue
        for name, factory in pages:
            widget = factory()
            alive.append(widget)
            _settle(app)
            path = out_root / label / f"{name}.png"
            try:
                _capture(widget, path, width, height, scale * args.scale_factor)
                written += 1
            except Exception as exc:  # noqa: BLE001 - report, do not abort the set
                print(f"  FAILED {path}: {type(exc).__name__}: {exc}")
        app.processEvents()

    shutil.rmtree(scratch, ignore_errors=True)
    print(f"{written} screenshot(s) written to {out_root}")
    sys.stdout.flush()

    # Leave immediately rather than unwinding Qt.
    #
    # Several pages start a background catalogue fetch in their constructor.
    # Those QThreads are still winding down when the interpreter tears down the
    # widgets that own them, and Windows aborts the process outright
    # (0xC0000409, STATUS_STACK_BUFFER_OVERRUN) — after the PNGs are safely on
    # disk, but with a non-zero exit code that would fail the CI job for no
    # real reason. Every file is written and flushed by this point, so there is
    # nothing left to clean up.
    os._exit(0 if written else 1)


def audit_for_secrets(root: Path) -> list[str]:
    """Read every rendered PNG back and report anything that must not ship.

    A belt-and-braces pass over the actual output rather than a claim about the
    input: OCR is not needed because what matters is whether a real key or a
    real address could have reached the render at all, and that is decided by
    the seeded data and the stubbed credentials. This checks the seed itself
    stayed fictional and that no forbidden page slipped into the set.
    """
    problems = []
    for png in sorted(root.rglob("*.png")):
        # Case-folded: --window names files after the view class
        # ("window-ConnectAgentsView.png"), so a case-sensitive match caught
        # only the older lowercase "05-setup-wizard.png" scheme and let every
        # whole-window capture through — the exact route this audit is meant
        # to backstop.
        stem = png.stem.lower()
        # "settings" is absent deliberately — see FORBIDDEN_PAGES, which this
        # mirrors. The Settings page stopped rendering keys in 1.2.1.
        if any(bad in stem for bad in ("wizard", "agent", "pair")):
            problems.append(f"{png}: page should never be published")
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
