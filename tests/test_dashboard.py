"""The Dashboard: which records it counts, and the page built from them.

The page is what the app opens on. It was a placeholder for its whole life
until this, so these pin what it now claims: problems and pending refunds are
counted by the same rules as Tracking and History, spend is Reports' figure
per currency, nothing crosses between test and production, and nothing is
fetched from EasyPost to draw it.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QScrollArea, QStackedWidget, QWidget
from PySide6.QtCore import Qt

from app.core.db import db_cursor, init_db
from app.services.dashboard import (
    PROBLEM_WINDOW_DAYS,
    RECENT_SHIPMENTS,
    build_summary,
    load_summary,
)
from app.services.formatting import format_money_map
from app.services.reports import total_spend_by_currency
from app.services.shipments import ShipmentRecord
from app.services.tracking import TrackerRecord

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _shipment(sid, **over) -> ShipmentRecord:
    fields = dict(
        id=sid, mode="test", status="purchased", to_address="Alex Morgan, London",
        from_address="Northwind Trading", carrier="RoyalMailV3",
        service="RoyalMail1stClass", rate_amount="4.45", rate_currency="GBP",
        tracking_code=f"TRK{sid}", label_url=None, insured_amount=None,
        refund_status=None,
    )
    fields.update(over)
    return ShipmentRecord(**fields)


def _tracker(tid, status, *, checked="2026-09-10 09:00:00", **over) -> TrackerRecord:
    fields = dict(
        id=tid, mode="test", tracking_code=f"TRK{tid}", carrier="USPS",
        status=status, status_detail=None, est_delivery_date=None,
        shipment_id=None, last_checked_at=checked,
    )
    fields.update(over)
    return TrackerRecord(**fields)


def _summary(shipments=(), trackers=()):
    return build_summary(list(shipments), list(trackers), mode="test", now=NOW)


# ---------------------------------------------------------------------------
# The rules, as a pure function
# ---------------------------------------------------------------------------


def test_a_new_install_is_empty():
    summary = _summary()
    assert summary.is_empty
    assert summary.spend_by_currency == {}


def test_trackers_alone_are_not_empty():
    """Someone who only follows parcels posted elsewhere still has a Dashboard."""
    assert not _summary(trackers=[_tracker("1", "in_transit")]).is_empty


def test_problems_are_exactly_what_tracking_calls_a_problem():
    trackers = [
        _tracker("fail", "failure", status_detail="damaged"),
        _tracker("rts", "return_to_sender"),
        _tracker("err", "error"),
        _tracker("ok", "delivered"),
        _tracker("moving", "in_transit"),
        _tracker("pickup", "available_for_pickup"),
    ]
    summary = _summary(trackers=trackers)
    assert [t.id for t in summary.problems] == ["fail", "rts", "err"]
    # Still being tracked: anything not in a final state.
    assert summary.open_trackers == 2


def test_an_old_problem_leaves_the_landing_page():
    """A terminal tracker is never polled again, so without a window a parcel
    that failed last year would sit at the top of the page for good."""
    inside = f"2026-08-{11 + 1:02d} 12:00:00"  # 30 days before NOW, less a day
    outside = "2026-08-11 11:59:59"  # a second past the window
    summary = _summary(trackers=[
        _tracker("recent", "failure", checked=inside),
        _tracker("stale", "failure", checked=outside),
    ])
    assert PROBLEM_WINDOW_DAYS == 30  # the dates above are written for 30
    assert [t.id for t in summary.problems] == ["recent"]


def test_a_problem_that_cannot_be_dated_is_shown_not_hidden():
    summary = _summary(trackers=[
        _tracker("none", "error", checked=None),
        _tracker("garbled", "error", checked="not a date"),
    ])
    assert [t.id for t in summary.problems] == ["none", "garbled"]


def test_only_a_submitted_refund_is_pending():
    summary = _summary(shipments=[
        _shipment("waiting", refund_status="submitted"),
        _shipment("done", refund_status="refunded"),
        _shipment("refused", refund_status="rejected"),
        _shipment("never", refund_status=None),
    ])
    assert [s.id for s in summary.pending_refunds] == ["waiting"]


def test_spend_is_never_summed_across_currencies():
    """3.85 GBP and 8.40 USD once reached two store listings as "12.25"."""
    shipments = [
        _shipment("gbp", rate_amount="3.85", rate_currency="GBP"),
        _shipment("usd", rate_amount="8.40", rate_currency="USD"),
    ]
    summary = _summary(shipments=shipments)
    assert summary.spend_by_currency == pytest.approx({"GBP": 3.85, "USD": 8.40})
    assert summary.spend_by_currency == total_spend_by_currency(shipments)
    assert format_money_map(summary.spend_by_currency) == "8.40 USD + 3.85 GBP"


def test_recent_shipments_are_the_newest_few_in_the_order_given():
    shipments = [_shipment(str(n)) for n in range(RECENT_SHIPMENTS + 3)]
    summary = _summary(shipments=shipments)
    assert [s.id for s in summary.recent_shipments] == [
        str(n) for n in range(RECENT_SHIPMENTS)
    ]
    assert summary.shipment_count == RECENT_SHIPMENTS + 3


# ---------------------------------------------------------------------------
# Against a seeded database
# ---------------------------------------------------------------------------


def setup_module(_module):
    init_db()


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        with db_cursor() as cur:
            cur.execute("DELETE FROM shipments")
            cur.execute("DELETE FROM trackers")

    wipe()
    yield
    wipe()


@contextmanager
def _mode(mode):
    """The active mode, as every module that reads it sees it. The network is
    a trap: nothing on the Dashboard may reach EasyPost."""
    manager = Mock()
    manager.active_mode = mode
    manager.get_client.side_effect = AssertionError("the Dashboard called EasyPost")
    with patch("app.services.dashboard.client_manager", manager), \
         patch("app.services.shipments.client_manager", manager), \
         patch("app.services.tracking.client_manager", manager):
        yield manager


def _seed(mode, *, suffix):
    with db_cursor() as cur:
        cur.executemany(
            "INSERT INTO shipments (id, mode, status, to_address, carrier, service,"
            " rate_amount, rate_currency, tracking_code, refund_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (f"shp_a{suffix}", mode, "purchased", "Alex Morgan, London",
                 "RoyalMailV3", "RoyalMail2ndClassSignedFor", "3.85", "GBP",
                 f"AA1{suffix}GB", None, "2026-09-10 08:00:00"),
                (f"shp_b{suffix}", mode, "purchased", "Sam Rivera, Manchester",
                 "USPS", "Priority", "8.40", "USD", f"EZ2{suffix}", "submitted",
                 "2026-09-10 09:00:00"),
            ],
        )
        cur.executemany(
            "INSERT INTO trackers (id, mode, tracking_code, carrier, status,"
            " status_detail, last_checked_at) VALUES (?,?,?,?,?,?,?)",
            [
                (f"trk_a{suffix}", mode, f"AA1{suffix}GB", "RoyalMailV3",
                 "return_to_sender", None, "2026-09-10 10:00:00"),
                (f"trk_b{suffix}", mode, f"EZ2{suffix}", "USPS", "in_transit",
                 None, "2026-09-10 10:00:00"),
            ],
        )


def test_each_mode_sees_only_its_own_records():
    _seed("test", suffix="T")
    _seed("production", suffix="P")
    with _mode("production"):
        _seed_extra = None  # noqa: F841  (production has the same two rows)
        production = load_summary(now=NOW)
    with _mode("test"):
        test = load_summary(now=NOW)

    assert production.mode == "production" and test.mode == "test"
    assert {s.id for s in test.recent_shipments} == {"shp_aT", "shp_bT"}
    assert {s.id for s in production.recent_shipments} == {"shp_aP", "shp_bP"}
    assert [t.id for t in test.problems] == ["trk_aT"]
    assert [s.id for s in test.pending_refunds] == ["shp_bT"]


def test_the_dashboard_spend_is_the_reports_spend():
    _seed("test", suffix="T")
    with _mode("test"):
        assert load_summary(now=NOW).spend_by_currency == total_spend_by_currency()


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _cells(table):
    return [
        table.item(r, c).text()
        for r in range(table.rowCount())
        for c in range(table.columnCount())
    ]


def test_a_new_install_is_offered_one_next_step(qapp):
    from app.ui.views.dashboard_view import DashboardView

    with _mode("test"):
        view = DashboardView()
    assert not view._empty_panel.isHidden()
    assert view._content.isHidden()

    asked = []
    view.create_shipment_requested.connect(lambda: asked.append(True))
    view._empty_create_button.click()
    assert asked == [True]


def test_the_page_shows_readable_values_not_api_codes(qapp):
    from app.ui.views.dashboard_view import DashboardView

    _seed("test", suffix="T")
    with _mode("test"):
        view = DashboardView()

    assert view._empty_panel.isHidden() and not view._content.isHidden()
    assert view._problems_figure.text() == "1"
    assert view._refunds_figure.text() == "1"
    assert view._open_figure.text() == "1"
    assert view._spend_figure.text() == "8.40 USD + 3.85 GBP"

    cells = _cells(view._attention_table) + _cells(view._recent_table)
    for raw in ("RoyalMailV3", "RoyalMail2ndClassSignedFor", "return_to_sender",
                "purchased", "submitted"):
        assert raw not in cells, f"{raw!r} reached the Dashboard unformatted"
    assert view._attention_table.rowCount() == 2
    assert view._recent_table.rowCount() == 2


@pytest.mark.parametrize("locale", ["en", "de"])
def test_the_page_fits_the_smallest_store_window(qapp, monkeypatch, locale):
    """1440 points wide, less the navigation sidebar's fixed 196. A page whose
    minimum width is more than that grows a horizontal scrollbar and cuts
    controls off its right-hand edge, and German is where that shows first."""
    import app.i18n
    from app.ui.views.dashboard_view import DashboardView

    monkeypatch.setattr(app.i18n, "current_locale", lambda: locale)
    _seed("test", suffix="T")
    with _mode("test"):
        view = DashboardView()
    assert view.minimumSizeHint().width() <= 1440 - 196


# ---------------------------------------------------------------------------
# Wiring into the window
# ---------------------------------------------------------------------------


def test_the_dashboard_refreshes_whenever_it_is_shown():
    """It is the page the app opens on and the page a mode switch lands on, so
    a Dashboard with no on-show refresh would show the previous mode's rows."""
    from app.ui.main_window import MainWindow

    window = Mock()
    sections = MainWindow._nav_sections(window)
    entries = {key: on_show for _, items in sections for key, _view, on_show in items}
    assert entries["main_window.nav_dashboard"] is window._dashboard_view.refresh


def test_the_dashboard_buttons_open_their_pages(qapp):
    from app.ui.main_window import MainWindow
    from app.ui.views.dashboard_view import DashboardView

    with _mode("test"):
        dashboard = DashboardView()
    window = SimpleNamespace(
        _dashboard_view=dashboard,
        _create_shipment_view=object(),
        _tracking_view=object(),
        _history_view=object(),
        _show_view=Mock(),
    )
    MainWindow._connect_dashboard(window)

    dashboard.create_shipment_requested.emit()
    dashboard.tracking_requested.emit()
    dashboard.history_requested.emit()
    assert [c.args[0] for c in window._show_view.call_args_list] == [
        window._create_shipment_view, window._tracking_view, window._history_view,
    ]


def test_show_view_selects_the_page_in_the_sidebar(qapp):
    """Through the sidebar, not the stack, so the highlight follows and the
    page's on-show refresh runs as it would for a click."""
    from app.ui.main_window import MainWindow

    nav, stack = QListWidget(), QStackedWidget()
    header = QListWidgetItem("Shipping")
    header.setData(Qt.ItemDataRole.UserRole, None)
    nav.addItem(header)
    pages = [QWidget(), QWidget()]
    for page in pages:
        holder = QScrollArea()
        holder.setWidget(page)
        item = QListWidgetItem("page")
        item.setData(Qt.ItemDataRole.UserRole, stack.addWidget(holder))
        nav.addItem(item)

    MainWindow._show_view(SimpleNamespace(_nav=nav, _view_stack=stack), pages[1])
    assert nav.currentRow() == 2
