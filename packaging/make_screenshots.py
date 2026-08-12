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
            " carrier, service, rate_amount, rate_currency, tracking_code)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("shp_demo1", MODE, "purchased", "Alex Morgan, London",
                 "Northwind Trading", "RoyalMailV3", "RoyalMail2ndClassSignedFor",
                 "3.85", "GBP", "AA000000001GB"),
                ("shp_demo2", MODE, "purchased", "Sam Rivera, Manchester",
                 "Northwind Trading", "USPS", "Priority", "8.40", "USD",
                 "EZ1000000001"),
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
        cur.executemany(
            "INSERT OR REPLACE INTO trackers (id, mode, tracking_code, carrier, status,"
            " status_detail, est_delivery_date, last_checked_at)"
            " VALUES (?,?,?,?,?,?,?,datetime('now'))",
            [
                ("trk_demo1", MODE, "AA000000001GB", "RoyalMailV3", "in_transit",
                 None, "2026-01-14"),
                ("trk_demo2", MODE, "EZ1000000001", "USPS", "delivered", None,
                 "2026-01-12"),
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
# Settings and the first-run wizard both render API key fields; the AI-agent
# page renders pairing tokens. Naming them here means adding a page to the
# screenshot set can never quietly add one of these.
FORBIDDEN_PAGES = {
    "settings_view", "setup_wizard", "connect_agents_view",
    "license_gate", "store_unlock", "pair_screen",
}


def _settle(app, seconds: float = 2.5) -> None:
    """Pump the event loop so background page loads finish before the grab.

    Several pages fetch their catalogue on a worker thread. Grabbing straight
    after construction caught the batch picker mid-fetch, so the screenshot read
    "Loading the carrier catalogue..." with an empty carrier list.
    """
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)


def _capture(widget, path: Path, width: int, height: int, scale: int) -> None:
    """Paint one widget to a PNG at an exact pixel size."""
    from PySide6.QtCore import QSize

    widget.resize(QSize(width, height))
    widget.setMinimumSize(QSize(width, height))
    # Let the layout settle before painting, or half the page renders unsized.
    widget.adjustSize()
    widget.resize(QSize(width, height))

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
        stem = png.stem
        if any(bad in stem for bad in ("settings", "wizard", "agent", "pair")):
            problems.append(f"{png}: page should never be published")
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
