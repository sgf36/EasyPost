"""Agent purchases whose outcome nobody knows, settled by asking EasyPost.

When the buy call times out, the request stays 'approved' so the daily limit
keeps counting it. Before this, nothing ever found out whether it had bought:
the request counted against the limit for the rest of the day, left the
approval list, and was shown nowhere. These pin that the app now looks the
purchase up by the ids it already holds, settles what EasyPost can prove, and
keeps showing what it cannot.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from easypost.errors import NotFoundError
from easypost.errors import TimeoutError as EasyPostTimeout
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core import mcp_approvals
from app.core.client import client_manager
from app.core.db import db_cursor, init_db
from app.core.settings import AppSettings
from app.services import mcp_runner


@pytest.fixture(autouse=True)
def _clean_queue():
    init_db()
    with db_cursor() as cur:
        cur.execute("DELETE FROM mcp_approvals")
        cur.execute("DELETE FROM mcp_audit")
        cur.execute("DELETE FROM shipments")
    yield


@pytest.fixture
def settings(monkeypatch):
    current = AppSettings(mcp_enabled=True, mcp_allow_spending=True,
                          mcp_max_purchase=50.0, mcp_daily_limit=100.0)
    current.mcp_limit_currency = "USD"
    monkeypatch.setattr(mcp_runner, "load_settings", lambda: current)
    return current


@pytest.fixture
def client(monkeypatch):
    fake = Mock()
    monkeypatch.setattr(client_manager, "get_client", lambda: fake)
    return fake


def spent():
    return mcp_approvals.spent_today(client_manager.active_mode, "USD")


def timed_out(client, action="buy_shipment", args=None, amount=15.0, age_seconds=600):
    """A request whose purchase call timed out `age_seconds` ago."""
    args = args if args is not None else {"shipment_id": "shp_1", "rate_id": "rate_1"}
    req = mcp_approvals.create_request(action, args, {}, amount, "USD" if amount else None)
    method = {"buy_shipment": client.shipment.buy, "buy_pickup": client.pickup.buy,
              "refund_shipment": client.shipment.refund}[action]
    if action == "buy_pickup":
        client.pickup.retrieve.return_value = SimpleNamespace(pickup_rates=[
            SimpleNamespace(id=args["rate_id"], carrier="FedEx", service="FDXPICKUP")])
    method.side_effect = EasyPostTimeout("timed out")
    with pytest.raises(EasyPostTimeout):
        mcp_runner.execute_approved(req.id)
    method.side_effect = None
    client.pickup.retrieve.reset_mock(return_value=True)
    age(req, age_seconds)
    return req


def age(req, seconds):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE mcp_approvals SET decided_at = datetime('now', ?) WHERE id = ?",
            (f"-{int(seconds)} seconds", req.id),
        )


def row(req):
    return mcp_approvals.get_request(req.id)


def bought_shipment(sid="shp_1"):
    return SimpleNamespace(
        id=sid, status="unknown", tracking_code="EZ1000", refund_status=None,
        selected_rate=SimpleNamespace(id="rate_1", carrier="USPS", service="Priority",
                                      rate="15.00", currency="USD"),
        postage_label=SimpleNamespace(label_url="https://example.invalid/label.png"),
        to_address=None, from_address=None, insurance=None,
    )


def unbought_shipment(sid="shp_1"):
    return SimpleNamespace(id=sid, status="unknown", tracking_code=None, refund_status=None,
                           selected_rate=None, postage_label=None)


# ------------------------------------------------------------------ labels

def test_timed_out_label_that_bought_is_settled_done_and_recorded(settings, client):
    req = timed_out(client)
    client.shipment.retrieve.return_value = bought_shipment()

    report = mcp_runner.reconcile_unknown_outcomes()

    client.shipment.retrieve.assert_called_once_with("shp_1")
    assert report.bought == [req.id] and report.not_bought == [] and report.unresolved == []
    assert row(req).status == "done"
    assert spent() == 15.0
    with db_cursor() as cur:
        cur.execute("SELECT mode, tracking_code FROM shipments WHERE id = 'shp_1'")
        saved = cur.fetchone()
    assert saved["tracking_code"] == "EZ1000" and saved["mode"] == req.mode


def test_timed_out_label_that_did_not_buy_is_rejected_and_releases_the_limit(settings, client):
    req = timed_out(client)
    assert spent() == 15.0
    client.shipment.retrieve.return_value = unbought_shipment()

    report = mcp_runner.reconcile_unknown_outcomes()

    assert report.not_bought == [req.id]
    assert row(req).status == "rejected"
    assert spent() == 0.0


def test_absence_is_not_trusted_straight_after_the_timeout(settings, client):
    """A buy EasyPost is still processing has no label yet. Finding none a
    few seconds after the timeout proves nothing, so it stays unknown."""
    req = timed_out(client, age_seconds=5)
    client.shipment.retrieve.return_value = unbought_shipment()

    report = mcp_runner.reconcile_unknown_outcomes()

    assert [r.id for r in report.unresolved] == [req.id]
    assert row(req).status == "approved" and spent() == 15.0


def test_a_purchase_is_believed_as_soon_as_it_is_found(settings, client):
    req = timed_out(client, age_seconds=5)
    client.shipment.retrieve.return_value = bought_shipment()
    assert mcp_runner.reconcile_unknown_outcomes().bought == [req.id]


def test_shipment_easypost_has_never_heard_of_was_not_bought(settings, client):
    req = timed_out(client)
    client.shipment.retrieve.side_effect = NotFoundError("not found", http_status=404)
    assert mcp_runner.reconcile_unknown_outcomes().not_bought == [req.id]
    assert row(req).status == "rejected"


def test_easypost_unreachable_leaves_it_unknown_and_listed(settings, client):
    req = timed_out(client)
    client.shipment.retrieve.side_effect = EasyPostTimeout("still down")

    report = mcp_runner.reconcile_unknown_outcomes()

    assert [r.id for r in report.unresolved] == [req.id]
    assert row(req).status == "approved" and spent() == 15.0
    assert [r.id for r in mcp_approvals.list_outcome_unknown()] == [req.id]


def test_settling_yesterdays_purchase_does_not_move_it_into_today(settings, client):
    """spent_today keys on decided_at; restamping it would charge an old
    purchase to today's limit."""
    req = timed_out(client, age_seconds=2 * 86400)
    client.shipment.retrieve.return_value = bought_shipment()
    mcp_runner.reconcile_unknown_outcomes()
    assert row(req).status == "done"
    assert spent() == 0.0


def test_a_saving_failure_still_settles_done(settings, client, monkeypatch):
    req = timed_out(client)
    client.shipment.retrieve.return_value = bought_shipment()
    monkeypatch.setattr(mcp_runner, "save_shipment_locally",
                        Mock(side_effect=RuntimeError("database is locked")))
    assert mcp_runner.reconcile_unknown_outcomes().bought == [req.id]
    assert row(req).status == "done"


# ------------------------------------------------------- other actions

def test_pickup_is_looked_up_and_settled(settings, client):
    req = timed_out(client, "buy_pickup", {"pickup_id": "pickup_1", "rate_id": "prate_1"},
                    amount=4.0)
    client.pickup.retrieve.return_value = SimpleNamespace(
        id="pickup_1", status="scheduled", confirmation="WTC123")
    assert mcp_runner.reconcile_unknown_outcomes().bought == [req.id]
    client.pickup.retrieve.assert_called_once_with("pickup_1")


def test_pickup_never_bought_is_rejected(settings, client):
    req = timed_out(client, "buy_pickup", {"pickup_id": "pickup_1", "rate_id": "prate_1"},
                    amount=4.0)
    client.pickup.retrieve.return_value = SimpleNamespace(
        id="pickup_1", status="unknown", confirmation=None)
    assert mcp_runner.reconcile_unknown_outcomes().not_bought == [req.id]


def test_cancelled_pickup_is_left_for_a_person(settings, client):
    """Cancelled could follow a purchase or not; EasyPost's record does not say."""
    req = timed_out(client, "buy_pickup", {"pickup_id": "pickup_1", "rate_id": "prate_1"},
                    amount=4.0)
    client.pickup.retrieve.return_value = SimpleNamespace(
        id="pickup_1", status="canceled", confirmation=None)
    assert [r.id for r in mcp_runner.reconcile_unknown_outcomes().unresolved] == [req.id]


def test_refund_request_is_settled_from_the_refund_status(settings, client):
    req = timed_out(client, "refund_shipment", {"shipment_id": "shp_1"}, amount=None)
    client.shipment.retrieve.return_value = SimpleNamespace(
        id="shp_1", refund_status="submitted", selected_rate=None, postage_label=None)
    assert mcp_runner.reconcile_unknown_outcomes().bought == [req.id]
    assert row(req).status == "done"


# ------------------------------------------------------------ selection

def test_nothing_to_check_makes_no_call(client):
    client.shipment.retrieve.side_effect = AssertionError("called EasyPost for nothing")
    report = mcp_runner.reconcile_unknown_outcomes()
    assert report.bought == report.not_bought == report.unresolved == []


def test_a_purchase_still_in_flight_is_left_alone(settings, client):
    """Claimed seconds ago with no error: another thread is buying it now."""
    req = mcp_approvals.create_request("buy_shipment", {"shipment_id": "shp_1", "rate_id": "r"},
                                       {}, 15.0, "USD")
    assert mcp_approvals.claim_for_execution(req.id)
    client.shipment.retrieve.side_effect = AssertionError("looked up a purchase in flight")
    assert mcp_runner.reconcile_unknown_outcomes().unresolved == []
    assert row(req).status == "approved"


def test_a_claim_abandoned_by_a_crash_is_checked(settings, client):
    """The app closed mid-purchase: approved, no error, long ago."""
    req = mcp_approvals.create_request("buy_shipment", {"shipment_id": "shp_1", "rate_id": "r"},
                                       {}, 15.0, "USD")
    assert mcp_approvals.claim_for_execution(req.id)
    age(req, mcp_runner.ABANDONED_AFTER_SECONDS + 60)
    client.shipment.retrieve.return_value = bought_shipment()
    assert mcp_runner.reconcile_unknown_outcomes().bought == [req.id]


def test_other_modes_requests_are_not_looked_up_with_this_modes_key(settings, client):
    req = timed_out(client)
    with db_cursor() as cur:
        cur.execute("UPDATE mcp_approvals SET mode = 'other' WHERE id = ?", (req.id,))
    client.shipment.retrieve.side_effect = AssertionError("wrong account")
    assert mcp_runner.reconcile_unknown_outcomes().unresolved == []


def test_a_request_settled_meanwhile_is_not_overwritten(settings, client, monkeypatch):
    req = timed_out(client)

    def settled_elsewhere(_sid):
        mcp_approvals.set_status(req.id, "done")
        return unbought_shipment()

    client.shipment.retrieve.side_effect = settled_elsewhere
    mcp_runner.reconcile_unknown_outcomes()
    assert row(req).status == "done"


# ------------------------------------------------------------ the page

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp, monkeypatch):
    import app.ui.views.connect_agents_view as module

    monkeypatch.setattr(module, "MCP_SUPPORTED", True)
    started = []

    def fake_run_async(fn, parent):
        task = SimpleNamespace(fn=fn, ok=[], err=[])
        task.succeeded = SimpleNamespace(connect=task.ok.append)
        task.failed = SimpleNamespace(connect=task.err.append)
        started.append(task)
        return task

    monkeypatch.setattr(module, "run_async", fake_run_async)
    widget = module.ConnectAgentsView()
    widget.started, widget.module = started, module
    return widget


def finish(task):
    try:
        result = task.fn()
    except Exception as exc:  # noqa: BLE001
        for cb in task.err:
            cb(exc)
    else:
        for cb in task.ok:
            cb(result)


def test_showing_the_page_checks_easypost_and_lists_what_is_still_unknown(view, settings, client):
    req = timed_out(client)
    client.shipment.retrieve.side_effect = EasyPostTimeout("still down")

    view.refresh()
    checks = [t for t in view.started if t.fn == view.module.reconcile_unknown_outcomes]
    assert checks, "opening the page did not ask EasyPost"
    finish(checks[-1])

    assert not view._unknown_group.isHidden()
    assert view._unknown_layout.count() == 1
    assert row(req).status == "approved"


def test_the_unknown_list_is_hidden_when_there_is_nothing_unknown(view):
    view.refresh_approvals()
    assert view._unknown_group.isHidden()


def test_startup_checks_and_offers_to_show_what_is_still_unknown(qapp, monkeypatch, settings, client):
    import app.ui.main_window as module

    req = timed_out(client)
    client.shipment.retrieve.side_effect = EasyPostTimeout("still down")
    tasks = []
    monkeypatch.setattr(module, "MCP_SUPPORTED", True)
    monkeypatch.setattr(module, "run_async", lambda fn, parent: tasks.append(fn) or SimpleNamespace(
        succeeded=SimpleNamespace(connect=lambda cb: tasks.append(cb)),
        failed=SimpleNamespace(connect=lambda cb: None)))
    asked = []
    monkeypatch.setattr(module.QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Yes)
    window = SimpleNamespace(_connect_agents_view=object(), _show_view=Mock())
    window._on_agent_purchases_reconciled = (
        lambda report: module.MainWindow._on_agent_purchases_reconciled(window, report))

    module.MainWindow._maybe_reconcile_agent_purchases(window)
    check, on_done = tasks
    on_done(check())

    assert asked, "an unknown purchase was not surfaced at startup"
    window._show_view.assert_called_once_with(window._connect_agents_view)
    assert row(req).status == "approved"
