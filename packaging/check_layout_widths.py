"""Does any captured view need more width than the window it is captured in?

The Mac App Store screenshots are rendered at 1440x900. The navigation sidebar
is a fixed 196 points, leaving roughly 1245 for the page. A page whose minimum
width exceeds that grows a horizontal scrollbar and starts cutting controls off
the right-hand edge — including controls in sections far above the offender,
which is what made this hard to find by eye.
"""
import importlib.util as ilu
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

spec = ilu.spec_from_file_location("ms", ROOT / "packaging" / "make_screenshots.py")
ms = ilu.module_from_spec(spec)
spec.loader.exec_module(ms)

os.environ["EASYPOST_DESKTOP_DATA_DIR"] = tempfile.mkdtemp(prefix="probe-")
ms._stub_credentials()

from app.config import DATABASE_PATH  # noqa: E402
from app.core.settings import load_settings, save_settings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
ms._seed_database(Path(str(DATABASE_PATH)))

AVAILABLE = 1245
LOCALES = ["en", "de", "es", "fr", "hi", "ja", "zh", "ru", "pl", "nl", "pt", "it"]
VIEWS = [
    ("CreateShipmentView", "app.ui.views.create_shipment_view"),
    ("TrackingView", "app.ui.views.tracking_view"),
    ("AddressBookView", "app.ui.views.address_book_view"),
    ("BatchView", "app.ui.views.batch_view"),
    ("HistoryView", "app.ui.views.history_view"),
    ("ReportsView", "app.ui.views.reports_view"),
    ("HtsLookupView", "app.ui.views.hts_lookup_view"),
    ("SettingsView", "app.ui.views.settings_view"),
    ("PickupsView", "app.ui.views.pickups_view"),
    ("InsuranceView", "app.ui.views.insurance_view"),
    ("ClaimsView", "app.ui.views.claims_view"),
    # Not capturable by make_screenshots: its navigation entry exists only on a
    # direct-download build held by a production licensee, and a screenshot run
    # has stubbed credentials and no licence. Constructing it works, which is
    # the whole reason this checker builds views itself rather than driving the
    # window — so the one page the harness cannot reach is still measured.
    ("AndroidAppView", "app.ui.views.android_app_view"),
]

import importlib  # noqa: E402
from app.i18n import clear_cache  # noqa: E402

worst = []
for locale in LOCALES:
    s = load_settings()
    s.locale = locale
    save_settings(s)
    clear_cache()
    line = []
    for name, module_path in VIEWS:
        module = importlib.import_module(module_path)
        importlib.reload(module)
        try:
            view = getattr(module, name)()
        except Exception as exc:  # noqa: BLE001
            line.append(f"{name}=ERR")
            print(f"  {locale}/{name}: {type(exc).__name__}: {exc}")
            continue
        width = view.minimumSizeHint().width()
        if width > AVAILABLE:
            worst.append((width, locale, name))
            line.append(f"{name}={width}!")
        view.deleteLater()
    print(f"{locale}: {' '.join(line) or 'all within budget'}")

print()
if worst:
    print("OVER BUDGET:")
    for width, locale, name in sorted(worst, reverse=True):
        print(f"  {width:5d}  {locale}/{name}  (budget {AVAILABLE})")
else:
    print(f"every view fits in {AVAILABLE} points in all {len(LOCALES)} locales")
sys.exit(1 if worst else 0)
