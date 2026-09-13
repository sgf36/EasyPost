"""Create Shipment's rates table, laid out as the customer's window lays it out.

The column helpers are tested in test_column_sizing.py against hand-built trees.
That is how the service column came to be 28px wide in German and 0px in Tamil
with every test green: the helper measured the text of column 0, the real view
puts each service name in a cell widget, and no test built the real view. The
Mac store screenshots render at 1440px, which hid it from the eye as well.

So this builds the real CreateShipmentView, in every language that ships, at the
page width the default 1100x720 window and the 880x600 minimum give it, applies
the app's theme, pushes real service names through the real rates handler, and
measures the labels the customer reads. It needs real fonts: run it on the
native platform plugin, never QT_QPA_PLATFORM=offscreen, which has no font
database and would measure empty boxes.
"""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QLabel

from app.core.settings import load_settings, save_settings
from app.i18n import SUPPORTED_LOCALES, clear_cache

# The page the view is given inside MainWindow: the window, less the fixed
# 196px navigation sidebar and the view stack's 18px margins either side
# (app/ui/main_window.py), less the mode banner's height.
NAV_WIDTH, STACK_MARGINS, BANNER = 196, 36, 40
WINDOWS = {"default 1100x720": (1100, 720), "minimum 880x600": (880, 600)}

# Real service codes EasyPost returned in test mode on 2026-09-13, for a US to
# US and a UK to UK parcel from one account, plus the long names customers
# actually buy from. Humanised by the view exactly as a live reply is.
BOUGHT_SERVICES = [
    ("RoyalMailV3", "RoyalMail1stClassLettersDailyRateservice", "6.05", "GBP", None),
    ("RoyalMailV3", "RoyalMail2ndClassLettersDailyRateservice", "4.85", "GBP", None),
    ("RoyalMailV3", "RoyalMailTracked48SignedForAgeVerification", "0.01", "GBP", None),
    ("RoyalMailV3", "RoyalMail24ParcelDailyRateService", "4.45", "GBP", None),
    ("FedExDefault", "FEDEX_INTERNATIONAL_PRIORITY_EXPRESS", "160.29", "USD", 1),
    ("UPSDAP", "NextDayAirEarlyAM", "150.86", "USD", 1),
    ("USPS", "GroundAdvantage", "7.04", "USD", 2),
]
# The longest name in that reply: an account-billed Royal Mail catalogue entry
# (63 characters). It must never be cut, but in a long language it may wrap.
CATALOGUE_EXTREME = ("RoyalMailV3", "DEImportTracked24ParcelBoxableHighVolumeWeekendService",
                     "0.01", "GBP", None)

LOCALES = [code for code, _english, _native in SUPPORTED_LOCALES]


@pytest.fixture(scope="module")
def themed_app():
    from app.ui.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    saved = (app.style().name(), app.font(), app.palette(), app.styleSheet())
    apply_theme(app)
    yield app
    app.setStyle(saved[0])
    app.setFont(saved[1])
    app.setPalette(saved[2])
    app.setStyleSheet(saved[3])


@pytest.fixture
def locale_setting():
    original = load_settings().locale

    def use(code):
        settings = load_settings()
        settings.locale = code
        save_settings(settings)
        clear_cache()

    yield use
    use(original)


def _pump(app, rounds=6):
    for _ in range(rounds):
        app.processEvents()


def _build(app, monkeypatch, width, height, services):
    import app.ui.views.create_shipment_view as V
    from app.core.db import init_db

    init_db()
    monkeypatch.setattr(V, "run_async", lambda fn, parent=None: SimpleNamespace(
        succeeded=SimpleNamespace(connect=lambda slot: None),
        failed=SimpleNamespace(connect=lambda slot: None)))
    monkeypatch.setattr(V, "list_predefined_packages", lambda: [])
    monkeypatch.setattr(V, "list_addresses", lambda: [
        SimpleNamespace(id="adr_from", label="Office", name="", city="London", state="",
                        country="GB", company="", phone="1"),
        SimpleNamespace(id="adr_to", label="Customer", name="", city="Leeds", state="",
                        country="GB", company="", phone="1"),
    ])
    view = V.CreateShipmentView()
    view.resize(QSize(width - NAV_WIDTH - STACK_MARGINS, height - BANNER))
    view.show()
    _pump(app)
    view._to_combo.setCurrentIndex(view._to_combo.findData("adr_to"))
    rates = [
        SimpleNamespace(id=f"rate_{i}", carrier=c, service=s, rate=r, currency=cur,
                        delivery_days=d, delivery_date_guaranteed=False)
        for i, (c, s, r, cur, d) in enumerate(services)
    ]
    view._on_rates_received(SimpleNamespace(id="shp_layout", rates=rates, messages=[]))
    tree = view._rates_tree
    for i in range(tree.topLevelItemCount()):
        tree.topLevelItem(i).setExpanded(True)
    _pump(app)
    return view


def _service_labels(view):
    tree = view._rates_tree
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for j in range(top.childCount()):
            cell = tree.itemWidget(top.child(j), 0)
            # Falls back to the first label so the same test can be pointed at an
            # older build of the view, as a positive control.
            label = cell.findChild(QLabel, "rateServiceName") or cell.findChild(QLabel)
            yield top.child(j), cell, label


@pytest.mark.parametrize("locale", LOCALES)
def test_service_names_fit_on_one_line_at_the_default_window(
        themed_app, locale_setting, monkeypatch, locale):
    locale_setting(locale)
    view = _build(themed_app, monkeypatch, *WINDOWS["default 1100x720"], BOUGHT_SERVICES)
    try:
        tree = view._rates_tree
        longest = max((label for _item, _cell, label in _service_labels(view)),
                      key=lambda label: label.fontMetrics().horizontalAdvance(label.text()))
        need = longest.fontMetrics().horizontalAdvance(longest.text())
        assert longest.width() >= need, (
            f"{locale}: '{longest.text()}' needs {need}px and has {longest.width()}px "
            f"(columns {[tree.columnWidth(c) for c in range(tree.columnCount())]})"
        )
        # The page itself must not scroll sideways, or the Buy column is off it.
        assert view._scroll.horizontalScrollBar().maximum() == 0, locale
    finally:
        view.close()
        view.deleteLater()


@pytest.mark.parametrize("window", sorted(WINDOWS))
@pytest.mark.parametrize("locale", LOCALES)
def test_no_service_name_is_ever_cut(themed_app, locale_setting, monkeypatch, locale, window):
    """Where a name cannot fit on one line it wraps, and the row is tall
    enough for every line of it. Clipped is never acceptable."""
    locale_setting(locale)
    view = _build(themed_app, monkeypatch, *WINDOWS[window],
                  BOUGHT_SERVICES + [CATALOGUE_EXTREME])
    try:
        tree = view._rates_tree
        for item, cell, label in _service_labels(view):
            assert label.height() >= label.heightForWidth(label.width()), (
                f"{locale} {window}: '{label.text()}' is cut to {label.height()}px "
                f"of the {label.heightForWidth(label.width())}px it needs"
            )
            row = tree.visualItemRect(item)
            assert cell.geometry().bottom() <= row.bottom() + 1, (locale, window, label.text())
        assert view._scroll.horizontalScrollBar().maximum() == 0, (locale, window)
    finally:
        view.close()
        view.deleteLater()
