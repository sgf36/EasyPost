"""The code that spends money when a person approves an AI agent's request.

Every test here runs execute_approved against the real approvals table and a
fake EasyPost client, because the defects it had were in the bookkeeping
around the purchase, not the purchase call: a bought label recorded as
rejected, a pickup that could never be bought, and a double click that could
buy twice.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from easypost.errors import TimeoutError as EasyPostTimeout

from app.core import mcp_approvals
from app.core.client import client_manager
from app.core.db import db_cursor, init_db
from app.core.settings import AppSettings
from app.services import mcp_runner, mcp_verify


@pytest.fixture(autouse=True)
def _clean_queue(monkeypatch):
    init_db()
    with db_cursor() as cur:
        cur.execute("DELETE FROM mcp_approvals")
        cur.execute("DELETE FROM mcp_audit")
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


@pytest.fixture
def no_local_save(monkeypatch):
    saved = []
    monkeypatch.setattr(mcp_runner, "save_shipment_locally", saved.append)
    return saved


def queue(action="buy_shipment", args=None, amount=15.0, currency="USD", summary=None):
    args = args if args is not None else {"shipment_id": "shp_1", "rate_id": "rate_1"}
    return mcp_approvals.create_request(action, args, summary or {}, amount, currency)


def status_of(request):
    return mcp_approvals.get_request(request.id).status


def spent():
    return mcp_approvals.spent_today(client_manager.active_mode, "USD")


def audit_outcomes():
    with db_cursor() as cur:
        cur.execute("SELECT outcome FROM mcp_audit ORDER BY id")
        return [row["outcome"] for row in cur.fetchall()]


# ----------------------------------------------------------------- pickups

def test_approved_pickup_is_bought_with_carrier_and_service_from_easypost(settings, client):
    client.pickup.retrieve.return_value = SimpleNamespace(pickup_rates=[
        SimpleNamespace(id="prate_other", carrier="UPS", service="Same-day"),
        SimpleNamespace(id="prate_9", carrier="USPS", service="NextDay"),
    ])
    client.pickup.buy.return_value = SimpleNamespace(id="pickup_1", status="scheduled")
    req = queue("buy_pickup", {"pickup_id": "pickup_1", "rate_id": "prate_9"}, 5.0)

    assert mcp_runner.execute_approved(req.id)["status"] == "done"

    client.pickup.buy.assert_called_once_with("pickup_1", carrier="USPS", service="NextDay")
    assert status_of(req) == "done"
    assert spent() == 5.0


def test_pickup_rate_that_has_gone_is_rejected_without_buying(settings, client):
    client.pickup.retrieve.return_value = SimpleNamespace(pickup_rates=[])
    req = queue("buy_pickup", {"pickup_id": "pickup_1", "rate_id": "prate_9"}, 5.0)

    with pytest.raises(ValueError, match="no longer one of the pickup rates"):
        mcp_runner.execute_approved(req.id)
    client.pickup.buy.assert_not_called()
    assert status_of(req) == "rejected"


@pytest.mark.parametrize("action, args, method", [
    ("buy_shipment", {"shipment_id": "shp_1", "rate_id": "rate_1"}, "shipment.buy"),
    ("refund_shipment", {"shipment_id": "shp_1"}, "shipment.refund"),
    ("buy_pickup", {"pickup_id": "pickup_1", "rate_id": "prate_1"}, "pickup.buy"),
])
def test_every_handler_reaches_the_easypost_client(action, args, method, client, no_local_save):
    """Runs each entry of the dispatch table through to the client, so a
    handler whose call does not match its service's signature fails here."""
    client.pickup.retrieve.return_value = SimpleNamespace(
        pickup_rates=[SimpleNamespace(id="prate_1", carrier="USPS", service="NextDay")]
    )
    mcp_runner._HANDLERS[action].purchase(args)
    group, name = method.split(".")
    getattr(getattr(client, group), name).assert_called_once()


def test_every_action_the_agent_can_file_has_a_handler():
    import app.mcp_server as server
    import inspect

    filed = set()
    for name in ("request_label_purchase", "request_pickup_purchase", "request_refund"):
        source = inspect.getsource(getattr(server, name))
        filed.update(a for a in mcp_approvals.SPENDING_ACTIONS if f'"{a}"' in source)
    assert filed == {"buy_shipment", "buy_pickup", "refund_shipment"}
    assert filed <= set(mcp_runner._HANDLERS)


# ------------------------------------------------ a purchase that succeeded

def test_label_bought_but_not_saved_is_done_counted_and_warned(settings, client, monkeypatch):
    client.shipment.buy.return_value = SimpleNamespace(id="shp_1", tracking_code="9400")

    def locked(_shipment):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mcp_runner, "save_shipment_locally", locked)
    req = queue(amount=15.0)

    outcome = mcp_runner.execute_approved(req.id)

    client.shipment.buy.assert_called_once()
    assert outcome == {"status": "done", "request_id": req.id, "warning": "database is locked"}
    stored = mcp_approvals.get_request(req.id)
    assert stored.status == "done"
    assert spent() == 15.0
    with db_cursor() as cur:
        cur.execute("SELECT result_json FROM mcp_approvals WHERE id = ?", (req.id,))
        assert "database is locked" in cur.fetchone()["result_json"]
    assert audit_outcomes()[-1] == "executed; local save failed: database is locked"


def test_clean_purchase_is_saved_and_counted(settings, client, no_local_save):
    client.shipment.buy.return_value = SimpleNamespace(id="shp_1")
    req = queue(amount=15.0)
    assert mcp_runner.execute_approved(req.id)["warning"] is None
    assert [s.id for s in no_local_save] == ["shp_1"]
    assert status_of(req) == "done" and spent() == 15.0


def test_easypost_refusal_is_rejected_and_not_counted(settings, client, no_local_save):
    refusal = Exception("rate expired")
    refusal.http_status = 422
    client.shipment.buy.side_effect = refusal
    req = queue(amount=15.0)

    with pytest.raises(Exception, match="rate expired"):
        mcp_runner.execute_approved(req.id)
    assert status_of(req) == "rejected"
    assert spent() == 0.0
    assert no_local_save == []


def test_timeout_is_not_written_off_and_still_counts(settings, client, no_local_save):
    """A timeout may have been charged on EasyPost's side; the ceiling must
    assume it was."""
    client.shipment.buy.side_effect = EasyPostTimeout("timed out")
    req = queue(amount=15.0)

    with pytest.raises(EasyPostTimeout):
        mcp_runner.execute_approved(req.id)
    stored = mcp_approvals.get_request(req.id)
    assert stored.status == "approved"
    assert spent() == 15.0
    assert "outcome unknown" in audit_outcomes()[-1]


@pytest.mark.parametrize("exc, charged_possible", [
    (type("E", (Exception,), {"http_status": 400})("bad"), False),
    (type("E", (Exception,), {"http_status": 408})("slow"), True),
    (type("E", (Exception,), {"http_status": 502})("gateway"), True),
    (EasyPostTimeout("timeout"), True),
    (KeyError("rate_id"), False),
])
def test_only_proof_of_refusal_counts_as_nothing_charged(exc, charged_possible):
    assert mcp_runner._nothing_was_charged(exc) is (not charged_possible)


# ----------------------------------------------------------------- atomicity

def test_second_approve_is_a_no_op(settings, client, no_local_save):
    client.shipment.buy.return_value = SimpleNamespace(id="shp_1")
    req = queue()

    mcp_runner.execute_approved(req.id)
    again = mcp_runner.execute_approved(req.id)

    assert again == {"status": "done", "request_id": req.id, "already_decided": True}
    assert client.shipment.buy.call_count == 1


def test_racing_approvals_buy_once(settings, client, no_local_save):
    calls = []

    def slow_buy(*args, **kwargs):
        calls.append(args)
        time.sleep(0.05)
        return SimpleNamespace(id="shp_1")

    client.shipment.buy.side_effect = slow_buy
    req = queue()
    start = threading.Barrier(6)
    results, errors = [], []

    def approve():
        start.wait()
        try:
            results.append(mcp_runner.execute_approved(req.id))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=approve) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(calls) == 1
    assert sum(1 for r in results if not r.get("already_decided")) == 1


def test_claim_is_conditional():
    req = queue()
    assert mcp_approvals.claim_for_execution(req.id) is True
    assert mcp_approvals.claim_for_execution(req.id) is False
    assert mcp_approvals.reject_if_pending(req.id) is False
    assert status_of(req) == "approved"


# ------------------------------------------------------------------ ceilings

def test_over_the_limit_goes_back_to_the_queue_unbought(settings, client):
    req = queue(amount=75.0)
    with pytest.raises(mcp_approvals.SpendLimitExceeded):
        mcp_runner.execute_approved(req.id)
    client.shipment.buy.assert_not_called()
    assert status_of(req) == "pending"


def test_daily_limit_counts_approvals_still_in_flight(settings, client):
    in_flight = queue(amount=90.0)
    assert mcp_approvals.claim_for_execution(in_flight.id)
    req = queue(amount=15.0)
    with pytest.raises(mcp_approvals.SpendLimitExceeded, match="daily limit"):
        mcp_runner.execute_approved(req.id)
    client.shipment.buy.assert_not_called()


def test_account_billed_needs_acknowledgement(settings, client, no_local_save):
    client.shipment.buy.return_value = SimpleNamespace(id="shp_rm")
    req = queue(amount=0.01, currency="GBP",
                summary={"carrier": "RoyalMailV3", "account_billed": True,
                         mcp_approvals.UNCHECKED_KEY: mcp_approvals.UNCHECKED_ACCOUNT_BILLED})

    with pytest.raises(mcp_approvals.CeilingNotChecked) as raised:
        mcp_runner.execute_approved(req.id)
    assert raised.value.reason == mcp_approvals.UNCHECKED_ACCOUNT_BILLED
    client.shipment.buy.assert_not_called()
    assert status_of(req) == "pending"

    assert mcp_runner.execute_approved(req.id, acknowledge_unchecked=True)["status"] == "done"
    client.shipment.buy.assert_called_once()
    assert any("limits not applied (account_billed)" in o for o in audit_outcomes())


def test_account_billed_flag_is_read_from_the_summary_not_the_amount(settings, client):
    """A sub-penny amount without the flag is an ordinary tiny price; with the
    flag, however small, it cannot pass without acknowledgement."""
    flagged = queue(amount=0.01, currency="USD", summary={"account_billed": True})
    with pytest.raises(mcp_approvals.CeilingNotChecked):
        mcp_runner.execute_approved(flagged.id)


def test_other_currency_needs_acknowledgement_and_is_not_summed(settings, client, no_local_save):
    client.shipment.buy.return_value = SimpleNamespace(id="shp_eu")
    req = queue(amount=45.0, currency="EUR")

    with pytest.raises(mcp_approvals.CeilingNotChecked) as raised:
        mcp_runner.execute_approved(req.id)
    assert raised.value.reason == mcp_approvals.UNCHECKED_CURRENCY

    mcp_runner.execute_approved(req.id, acknowledge_unchecked=True)
    assert spent() == 0.0
    assert mcp_approvals.spent_today(client_manager.active_mode, "EUR") == 45.0


def test_unknown_price_is_refused_even_when_acknowledged(settings, client):
    req = queue(amount=None, currency="USD")
    with pytest.raises(mcp_approvals.SpendLimitExceeded, match="could not be read"):
        mcp_runner.execute_approved(req.id, acknowledge_unchecked=True)
    client.shipment.buy.assert_not_called()


def test_refund_has_no_price_and_is_not_held_to_the_ceilings(settings, client):
    client.shipment.refund.return_value = SimpleNamespace(id="shp_1", status="submitted")
    req = queue("refund_shipment", {"shipment_id": "shp_1"}, amount=None, currency=None)
    assert mcp_runner.execute_approved(req.id)["status"] == "done"
    client.shipment.refund.assert_called_once_with("shp_1")


# ------------------------------------------------------------ re-validation

def test_spending_switched_off_since_queueing_rejects(settings, client):
    settings.mcp_allow_spending = False
    req = queue()
    with pytest.raises(PermissionError, match="switched off"):
        mcp_runner.execute_approved(req.id)
    assert status_of(req) == "rejected"
    client.shipment.buy.assert_not_called()


def test_mode_changed_since_queueing_rejects(settings, client):
    req = queue()
    other = "production" if client_manager.active_mode == "test" else "test"
    with db_cursor() as cur:
        cur.execute("UPDATE mcp_approvals SET mode = ? WHERE id = ?", (other, req.id))
    with pytest.raises(PermissionError, match="mode"):
        mcp_runner.execute_approved(req.id)
    assert status_of(req) == "rejected"
    client.shipment.buy.assert_not_called()


def test_unknown_request_and_unsupported_action(settings, client):
    with pytest.raises(ValueError, match="no longer exists"):
        mcp_runner.execute_approved("apr_missing")
    req = queue("buy_batch", {"batch_id": "batch_1"})
    with pytest.raises(ValueError, match="Unsupported"):
        mcp_runner.execute_approved(req.id)
    assert status_of(req) == "rejected"


# -------------------------------------------------- what the agent can queue

def _shipment_with(rate):
    return SimpleNamespace(rates=[rate], to_address=None, from_address=None)


def test_verify_marks_royal_mail_account_billing(client):
    client.shipment.retrieve.return_value = _shipment_with(SimpleNamespace(
        id="rate_rm", carrier="RoyalMailV3", service="SpecialDeliveryGuaranteed1pm",
        rate="0.01", currency="GBP", delivery_days=1))
    summary, amount, currency = mcp_verify.verify_shipment_purchase("shp", "rate_rm")
    assert summary["account_billed"] is True
    assert (amount, currency) == (0.01, "GBP")


def test_verify_refuses_a_placeholder_rate(client):
    client.shipment.retrieve.return_value = _shipment_with(SimpleNamespace(
        id="rate_ph", carrier="USPS", service="Catalogue", rate="0.01", currency="USD",
        delivery_days=None))
    with pytest.raises(ValueError, match="placeholder"):
        mcp_verify.verify_shipment_purchase("shp", "rate_ph")


def test_verify_pickup_refuses_a_placeholder_rate(client):
    client.pickup.retrieve.return_value = SimpleNamespace(pickup_rates=[SimpleNamespace(
        id="prate", carrier="UPS", service="Pickup", rate="0.01", currency="USD")])
    with pytest.raises(ValueError, match="placeholder"):
        mcp_verify.verify_pickup_purchase("pickup_1", "prate")


@pytest.fixture
def server(monkeypatch, settings):
    import app.mcp_server as server

    monkeypatch.setattr(server, "load_settings", lambda: settings)
    return server


def test_forty_account_billed_labels_are_queued_as_unchecked_not_passed(server, settings):
    """The audit's scenario: a 0.50 ceiling used to let 40 Royal Mail labels
    through as 0.01 each. Now each is queued with the reason attached, and
    none can be carried out without the approver accepting it."""
    settings.mcp_max_purchase = 0.50
    settings.mcp_daily_limit = 0.50
    verifier = lambda: ({"carrier": "RoyalMailV3", "account_billed": True}, 0.01, "GBP")  # noqa: E731
    for _ in range(40):
        server._file_request("buy_shipment", {"shipment_id": "s", "rate_id": "r"}, verifier)
    queued = mcp_approvals.list_pending()
    assert len(queued) == 40
    assert all(q.summary[mcp_approvals.UNCHECKED_KEY] == "account_billed" for q in queued)
    with pytest.raises(mcp_approvals.CeilingNotChecked):
        mcp_runner.execute_approved(queued[0].id)


def test_file_request_refuses_an_unreadable_price(server):
    verifier = lambda: ({"carrier": "USPS"}, None, "USD")  # noqa: E731
    with pytest.raises(PermissionError, match="could not be read"):
        server._file_request("buy_shipment", {"shipment_id": "s", "rate_id": "r"}, verifier)
    assert mcp_approvals.list_pending() == []


def test_file_request_passes_a_priced_purchase_without_a_flag(server):
    verifier = lambda: ({"carrier": "USPS"}, 12.0, "usd")  # noqa: E731
    server._file_request("buy_shipment", {"shipment_id": "s", "rate_id": "r"}, verifier)
    (queued,) = mcp_approvals.list_pending()
    assert mcp_approvals.UNCHECKED_KEY not in queued.summary


def test_file_request_queues_a_refund_without_a_price(server):
    verifier = lambda: ({"kind": "refund"}, None, None)  # noqa: E731
    server._file_request("refund_shipment", {"shipment_id": "s"}, verifier)
    assert len(mcp_approvals.list_pending()) == 1
