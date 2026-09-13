"""Which labels count as spend, on Reports and the Dashboard.

A refunded label used to count as spend for good: three 5.00 labels, two of
them refunded, reported 15.00 spent. These pin the rule both pages now share:
a refund the carrier has confirmed leaves spend, a refund still awaiting the
carrier stays in spend and is shown on its own, and nothing is ever added
across currencies.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from app.core.db import db_cursor, init_db
from app.services import reports
from app.services.dashboard import build_summary
from app.services.shipments import ShipmentRecord

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _shipment(sid, amount="5.00", currency="USD", refund=None, **over) -> ShipmentRecord:
    fields = dict(
        id=sid, mode="test", status="purchased", to_address="Alex Morgan, London",
        from_address="Northwind Trading", carrier="USPS", service="Priority",
        rate_amount=amount, rate_currency=currency, tracking_code=f"TRK{sid}",
        label_url=None, insured_amount=None, refund_status=refund,
    )
    fields.update(over)
    return ShipmentRecord(**fields)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_refunded_label_is_not_spend():
    """The audit's reproduction: 15.00 reported where 5.00 was spent."""
    records = [
        _shipment("kept"),
        _shipment("back", refund="refunded"),
        _shipment("back_upper", refund="REFUNDED"),
    ]
    assert reports.total_spend_by_currency(records) == pytest.approx({"USD": 5.0})
    assert reports.refunded_by_currency(records) == pytest.approx({"USD": 10.0})


def test_a_submitted_refund_still_counts_and_is_shown_on_its_own():
    """The carrier can still say no, so the money is not back yet."""
    records = [_shipment("kept"), _shipment("waiting", amount="7.25", refund="submitted")]
    assert reports.total_spend_by_currency(records) == pytest.approx({"USD": 12.25})
    assert reports.pending_refund_by_currency(records) == pytest.approx({"USD": 7.25})
    assert reports.refunded_by_currency(records) == {}


def test_a_rejected_refund_is_spend():
    records = [_shipment("refused", refund="rejected")]
    assert reports.total_spend_by_currency(records) == pytest.approx({"USD": 5.0})
    assert reports.pending_refund_by_currency(records) == {}


def test_refund_figures_are_never_summed_across_currencies():
    records = [
        _shipment("gbp", amount="3.85", currency="GBP", refund="submitted"),
        _shipment("usd", amount="8.40", currency="USD", refund="submitted"),
        _shipment("gbp_back", amount="2.00", currency="GBP", refund="refunded"),
    ]
    assert reports.pending_refund_by_currency(records) == pytest.approx({"GBP": 3.85, "USD": 8.40})
    assert reports.refunded_by_currency(records) == pytest.approx({"GBP": 2.00})


def test_the_chart_leaves_out_refunded_labels_too():
    """Otherwise the bars would add up to more than the total above them."""
    records = [
        _shipment("kept", carrier="USPS"),
        _shipment("back", carrier="UPS", refund="refunded"),
    ]
    assert reports.spend_by_carrier(records) == {"USPS": pytest.approx({"USD": 5.0})}
    assert reports.primary_currency([_shipment("back", refund="refunded")]) == ""


def test_a_refunded_label_was_still_purchased():
    records = [_shipment("kept"), _shipment("back", refund="refunded")]
    assert reports.total_labels_purchased(records) == 2


def test_the_dashboard_uses_the_same_rule():
    records = [
        _shipment("kept"),
        _shipment("back", refund="refunded"),
        _shipment("waiting", amount="2.50", refund="submitted"),
    ]
    summary = build_summary(records, [], mode="test", now=NOW)
    assert summary.spend_by_currency == pytest.approx({"USD": 7.5})
    assert summary.pending_refund_by_currency == pytest.approx({"USD": 2.5})


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean():
    init_db()

    def wipe():
        with db_cursor() as cur:
            cur.execute("DELETE FROM shipments")
            cur.execute("DELETE FROM trackers")

    wipe()
    yield
    wipe()


@contextmanager
def _mode(mode="test"):
    manager = Mock()
    manager.active_mode = mode
    manager.get_client.side_effect = AssertionError("a report called EasyPost")
    with patch("app.services.dashboard.client_manager", manager), \
         patch("app.services.shipments.client_manager", manager), \
         patch("app.services.tracking.client_manager", manager):
        yield manager


def _seed():
    with db_cursor() as cur:
        cur.executemany(
            "INSERT INTO shipments (id, mode, status, carrier, service, rate_amount,"
            " rate_currency, tracking_code, refund_status) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("shp_kept", "test", "purchased", "USPS", "Priority", "5.00", "USD", "EZ1", None),
                ("shp_back", "test", "purchased", "USPS", "Priority", "10.00", "USD", "EZ2",
                 "refunded"),
                ("shp_wait", "test", "purchased", "USPS", "Priority", "4.00", "USD", "EZ3",
                 "submitted"),
            ],
        )


def test_reports_page_excludes_refunds_and_says_what_is_awaited(qapp):
    from app.services.formatting import format_money
    from app.ui.views.reports_view import ReportsView

    _seed()
    with _mode():
        view = ReportsView()
    summary = view._summary_label.text()
    assert format_money(9.0, "USD") in summary
    assert format_money(15.0, "USD") not in summary and format_money(19.0, "USD") not in summary
    refunds = view._refunds_label.text()
    assert format_money(4.0, "USD") in refunds
    assert format_money(10.0, "USD") in refunds


def test_reports_reads_the_shipments_once_per_refresh(qapp, monkeypatch):
    from app.services import shipments
    from app.ui.views.reports_view import ReportsView

    _seed()
    with _mode():
        view = ReportsView()
        reads = []
        real = shipments.list_shipments
        monkeypatch.setattr(reports, "list_shipments", lambda: reads.append(1) or real())
        import app.ui.views.reports_view as module
        if hasattr(module, "list_shipments"):
            monkeypatch.setattr(module, "list_shipments", lambda: reads.append(1) or real())
        view.refresh()
    assert len(reads) == 1


def test_dashboard_spend_card_excludes_refunds_and_notes_the_awaited_part(qapp):
    from app.services.formatting import format_money
    from app.ui.views.dashboard_view import DashboardView

    _seed()
    with _mode():
        view = DashboardView()
    assert view._spend_figure.text() == format_money(9.0, "USD")
    assert not view._spend_note.isHidden()
    assert format_money(4.0, "USD") in view._spend_note.text()


def test_dashboard_spend_note_is_hidden_with_nothing_awaited(qapp):
    from app.ui.views.dashboard_view import DashboardView

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO shipments (id, mode, status, carrier, rate_amount, rate_currency,"
            " tracking_code) VALUES ('shp_only', 'test', 'purchased', 'USPS', '5.00', 'USD', 'EZ9')"
        )
    with _mode():
        view = DashboardView()
    assert view._spend_note.isHidden()
