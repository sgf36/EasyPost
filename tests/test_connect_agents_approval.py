"""The Approve button: what the person is asked, and what is passed on.

The service refuses a purchase the spending limits could not measure unless
it is told the person accepted that. These pin the other half: the view only
says so after asking a question that states the reason, and never shows a
Royal Mail marker figure as if it were the price.
"""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from app.core import mcp_approvals
from app.core.db import init_db
from app.i18n import tr


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp, monkeypatch):
    init_db()
    import app.ui.views.connect_agents_view as module

    # A source checkout has no mcp_supported.flag, so the page would otherwise
    # render its "not available in this version" notice instead of the queue.
    monkeypatch.setattr(module, "MCP_SUPPORTED", True)
    started = []

    def fake_run_async(fn, parent):
        started.append(fn)
        return SimpleNamespace(succeeded=SimpleNamespace(connect=lambda cb: None),
                               failed=SimpleNamespace(connect=lambda cb: None))

    monkeypatch.setattr(module, "run_async", fake_run_async)
    executed = []
    monkeypatch.setattr(module, "execute_approved",
                        lambda rid, acknowledge_unchecked=False: executed.append(
                            (rid, acknowledge_unchecked)) or {"status": "done"})
    widget = module.ConnectAgentsView()
    widget.started, widget.executed, widget.module = started, executed, module
    return widget


def request(reason=None, account_billed=False, currency="GBP"):
    summary = {"carrier": "RoyalMailV3", "service": "SpecialDelivery", "price": "0.01",
               "currency": currency, "mode": "test", "account_billed": account_billed}
    if reason:
        summary[mcp_approvals.UNCHECKED_KEY] = reason
    return SimpleNamespace(id="apr_1", action="buy_shipment", summary=summary,
                           amount=0.01, currency=currency)


def answer(monkeypatch, module, reply, asked):
    def question(_parent, _title, body, *_args):
        asked.append(body)
        return reply
    monkeypatch.setattr(module.QMessageBox, "question", question)


def run_started(view):
    for fn in view.started:
        fn()


def test_account_billed_approval_states_the_reason_and_acknowledges(view, monkeypatch):
    asked = []
    answer(monkeypatch, view.module, QMessageBox.StandardButton.Yes, asked)
    view._on_approve(request(mcp_approvals.UNCHECKED_ACCOUNT_BILLED, account_billed=True))
    run_started(view)

    reason = tr("connect_agents.unchecked_account_billed", carrier="RoyalMailV3")
    assert reason in asked[0]
    assert "0.01" not in asked[0]
    assert view.executed == [("apr_1", True)]


def test_declining_the_unchecked_question_buys_nothing(view, monkeypatch):
    answer(monkeypatch, view.module, QMessageBox.StandardButton.No, [])
    view._on_approve(request(mcp_approvals.UNCHECKED_CURRENCY))
    run_started(view)
    assert view.executed == []


def test_ordinary_approval_does_not_acknowledge_anything(view, monkeypatch):
    asked = []
    answer(monkeypatch, view.module, QMessageBox.StandardButton.Yes, asked)
    view._on_approve(request(currency="USD"))
    run_started(view)
    assert view.executed == [("apr_1", False)]
    assert "0.01" in asked[0]


def test_request_queued_without_a_reason_is_asked_again_when_the_service_refuses(view, monkeypatch):
    asked = []
    answer(monkeypatch, view.module, QMessageBox.StandardButton.Yes, asked)
    view._on_approval_failed(request(currency="EUR"),
                             mcp_approvals.CeilingNotChecked(mcp_approvals.UNCHECKED_CURRENCY))
    run_started(view)
    assert "EUR" in asked[0]
    assert view.executed == [("apr_1", True)]


def test_card_shows_billed_to_account_instead_of_the_marker(view):
    card = view._build_approval_card(
        request(mcp_approvals.UNCHECKED_ACCOUNT_BILLED, account_billed=True))
    text = " ".join(label.text() for label in card.findChildren(QLabel))
    assert tr("create_shipment.billed_to_account") in text
    assert "0.01" not in text
    assert tr("connect_agents.unchecked_account_billed", carrier="RoyalMailV3") in text


def test_bought_but_not_saved_is_reported(view, monkeypatch):
    shown = []
    monkeypatch.setattr(view.module.QMessageBox, "warning",
                        lambda _p, _t, body: shown.append(body))
    view._on_approval_finished({"status": "done", "warning": "database is locked"})
    assert shown == [tr("connect_agents.bought_not_saved", error="database is locked")]


def test_limit_currency_is_saved(view):
    from app.core.settings import load_settings

    view._limit_currency.setCurrentText("GBP")
    assert load_settings().mcp_limit_currency == "GBP"
