"""Labels bought through a batch must reach History, Tracking and the reports.

``batch.buy`` answers before it has bought anything. Verified live in test mode
on 2026-09-13: the response is ``state: created`` with every shipment stub
``queued_for_purchase`` and no tracking code, and the stubs turn
``postage_purchased`` one at a time over the following seconds while the batch
itself still says ``created``. The view recorded shipments once, from that
response, so it recorded nothing; the polling that followed stopped at once,
because ``created`` is not a transitional state, and never recorded either.
Every label bought in bulk was paid for and then invisible to the app.

Batches here are built with the SDK's own ``convert_to_easypost_object``, as in
test_batch_failures.py, so the code is exercised against the real response type.
"""

from types import SimpleNamespace

import pytest
from easypost.easypost_object import convert_to_easypost_object
from PySide6.QtWidgets import QApplication

from app.core.db import db_cursor, init_db
from app.services import batches
from app.services.shipments import list_shipments
from app.services.tracking import list_trackers
import app.ui.views.batch_view as batch_view_module


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with db_cursor() as cur:
        for table in ("shipments", "trackers", "batches"):
            cur.execute(f"DELETE FROM {table}")
    yield


def _batch(shipments, state, batch_id="batch_1"):
    return convert_to_easypost_object(
        {"id": batch_id, "object": "Batch", "state": state,
         "num_shipments": len(shipments), "shipments": shipments}
    )


def _stub(shipment_id, status, tracking_code=None, message=None):
    stub = {"id": shipment_id, "batch_status": status, "reference": f"ref-{shipment_id}"}
    if tracking_code:
        stub["tracking_code"] = tracking_code
    if message:
        stub["batch_message"] = message
    return stub


# The three moments of one purchase, as observed live.
QUEUED = _batch([_stub("shp_1", "queued_for_purchase"),
                 _stub("shp_2", "queued_for_purchase")], state="created")
HALF_BOUGHT = _batch([_stub("shp_1", "postage_purchased", "EZ1000000001"),
                      _stub("shp_2", "queued_for_purchase")], state="created")
SETTLED = _batch([_stub("shp_1", "postage_purchased", "EZ1000000001"),
                  _stub("shp_2", "postage_purchased", "EZ1000000002")], state="purchased")


class _FakeClient:
    """Answers shipment and batch retrieval, and counts the shipment requests —
    each one is a network call per label, which is what idempotence must save."""

    def __init__(self, batches_by_id=None):
        self.retrieved: list[str] = []
        self.batches_by_id = dict(batches_by_id or {})
        self.shipment = SimpleNamespace(retrieve=self._retrieve_shipment)
        self.batch = SimpleNamespace(retrieve=self._retrieve_batch)

    def _retrieve_shipment(self, shipment_id):
        self.retrieved.append(shipment_id)
        n = shipment_id.split("_")[-1]
        return convert_to_easypost_object({
            "id": shipment_id, "object": "Shipment", "status": "unknown",
            "tracking_code": f"EZ100000000{n}",
            "selected_rate": {"carrier": "USPS", "service": "GroundAdvantage",
                              "rate": "6.00", "currency": "USD"},
            "postage_label": {"label_url": f"https://example.test/{n}.png"},
            "tracker": {"id": f"trk_{n}", "object": "Tracker",
                        "tracking_code": f"EZ100000000{n}", "shipment_id": shipment_id},
        })

    def _retrieve_batch(self, batch_id):
        return self.batches_by_id[batch_id]


@pytest.fixture
def client(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(batches.client_manager, "get_client", lambda: fake)
    return fake


class _SyncTask:
    """run_async, minus the thread: the result is delivered on connect."""

    def __init__(self, fn):
        try:
            self._result, self._error = fn(), None
        except Exception as exc:  # noqa: BLE001
            self._result, self._error = None, exc
        self.succeeded = SimpleNamespace(connect=self._on_succeeded)
        self.failed = SimpleNamespace(connect=self._on_failed)

    def _on_succeeded(self, callback):
        if self._error is None:
            callback(self._result)

    def _on_failed(self, callback):
        if self._error is not None:
            callback(self._error)


@pytest.fixture
def view(qapp, client, monkeypatch):
    monkeypatch.setattr(batch_view_module, "run_async", lambda fn, parent=None: _SyncTask(fn))
    monkeypatch.setattr(batch_view_module, "list_addresses", lambda: [])
    monkeypatch.setattr(batch_view_module.ServicePicker, "load_catalogue", lambda self: None)
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(batch_view_module.QMessageBox, name, lambda *a, **k: None)
    monkeypatch.setattr(batch_view_module, "note_successful_shipment", lambda: None)
    monkeypatch.setattr(batch_view_module, "schedule_review_prompt", lambda parent: None)
    monkeypatch.setattr(batch_view_module, "mark_session_friction", lambda: None)
    # Never shown: the page the user has navigated away from is exactly this.
    return batch_view_module.BatchView()


def _history_ids():
    return sorted(record.id for record in list_shipments())


# ---------------------------------------------------------------------------
# The view: record from the batch as it settles, not from the buy response
# ---------------------------------------------------------------------------


def test_a_bought_batch_reaches_history_once_it_settles(view, client):
    view._on_batch_bought(QUEUED)
    assert _history_ids() == []  # nothing is bought yet, so nothing to record

    # Polling must survive the buy response, whose batch state is "created".
    assert view._poll_timer.isActive()

    view._on_poll_result(SETTLED)
    assert _history_ids() == ["shp_1", "shp_2"]
    assert not view._poll_timer.isActive()


def test_shipments_are_recorded_as_each_one_is_bought(view, client):
    view._on_batch_bought(QUEUED)
    view._on_poll_result(HALF_BOUGHT)
    assert _history_ids() == ["shp_1"]
    assert view._poll_timer.isActive()  # shp_2 is still queued

    view._on_poll_result(SETTLED)
    assert _history_ids() == ["shp_1", "shp_2"]


def test_each_label_is_fetched_and_recorded_exactly_once(view, client):
    view._on_batch_bought(QUEUED)
    view._on_poll_result(HALF_BOUGHT)
    view._on_poll_result(SETTLED)
    # Refresh Status, a pushed webhook event and the hosted label merge all
    # hand the settled batch back again.
    view._on_status_refreshed(SETTLED)
    view._on_batch_bought(SETTLED)

    recorded = [sid for sid in client.retrieved if sid in ("shp_1", "shp_2")]
    history_fetches = recorded.count("shp_1"), recorded.count("shp_2")
    # Label URLs for the print sheet are fetched once more per label on top of
    # recording; that is the only other retrieval allowed.
    assert history_fetches == (2, 2)
    assert _history_ids() == ["shp_1", "shp_2"]


def test_a_partial_batch_records_the_labels_that_were_bought(view, client):
    """A failed row used to make the view return before recording anything,
    so the labels that did buy were lost along with the one that did not."""
    view._on_batch_bought(_batch([
        _stub("shp_1", "queued_for_purchase"),
        _stub("shp_2", "creation_failed", message="Invalid address"),
    ], state="created"))
    assert view._poll_timer.isActive()

    view._on_poll_result(_batch([
        _stub("shp_1", "postage_purchased", "EZ1000000001"),
        _stub("shp_2", "creation_failed", message="Invalid address"),
    ], state="purchased"))
    assert _history_ids() == ["shp_1"]


def test_declining_auto_track_still_records_history(view, client):
    view._selection = SimpleNamespace(auto_track=False)
    view._on_batch_bought(QUEUED)
    view._on_poll_result(SETTLED)
    assert _history_ids() == ["shp_1", "shp_2"]
    assert list_trackers() == []


def test_accepting_auto_track_records_the_trackers(view, client):
    view._selection = SimpleNamespace(auto_track=True)
    view._on_batch_bought(QUEUED)
    view._on_poll_result(SETTLED)
    assert sorted(t.id for t in list_trackers()) == ["trk_1", "trk_2"]


def test_an_unbought_batch_is_not_polled_forever(view, client):
    """Before purchase the stubs say "created", not "queued_for_purchase", so
    a created batch waiting for the Buy button must not keep the timer alive."""
    view._on_status_refreshed(_batch([_stub("shp_1", "created")], state="created"))
    assert not view._poll_timer.isActive()


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


def test_purchase_in_progress_follows_the_stubs_not_the_batch_state():
    assert batches.purchase_in_progress(QUEUED)
    assert batches.purchase_in_progress(HALF_BOUGHT)
    assert not batches.purchase_in_progress(SETTLED)
    assert not batches.purchase_in_progress(
        _batch([_stub("shp_1", "created")], state="created"))
    assert batches.purchase_in_progress(_batch([], state="purchasing"))


def test_recording_twice_neither_duplicates_nor_refetches(client):
    assert batches.record_batch_shipments(SETTLED) == (2, 2)
    assert batches.record_batch_shipments(SETTLED) == (0, 0)
    assert client.retrieved == ["shp_1", "shp_2"]
    assert _history_ids() == ["shp_1", "shp_2"]


# ---------------------------------------------------------------------------
# Backfill: a batch bought in an earlier session that was never recorded
# ---------------------------------------------------------------------------


def _saved_batch(batch, auto_track=None):
    batches.save_batch_locally(batch, "orders.csv", auto_track=auto_track)


def test_a_batch_left_unrecorded_by_an_earlier_session_is_backfilled(client):
    _saved_batch(QUEUED)  # what the old code left behind: saved, never recorded
    client.batches_by_id["batch_1"] = SETTLED

    assert batches.backfill_batch_shipments() == 2
    assert _history_ids() == ["shp_1", "shp_2"]

    # Done once: the next launch does not go back to EasyPost for it.
    client.retrieved.clear()
    client.batches_by_id.clear()
    assert batches.backfill_batch_shipments() == 0
    assert client.retrieved == []


def test_backfill_leaves_a_purchase_still_in_progress_for_later(client):
    _saved_batch(QUEUED)
    client.batches_by_id["batch_1"] = HALF_BOUGHT
    assert batches.backfill_batch_shipments() == 1

    client.batches_by_id["batch_1"] = SETTLED
    assert batches.backfill_batch_shipments() == 1
    assert _history_ids() == ["shp_1", "shp_2"]


def test_backfill_honours_the_auto_track_choice_made_at_creation(client):
    _saved_batch(QUEUED, auto_track=False)
    client.batches_by_id["batch_1"] = SETTLED
    batches.backfill_batch_shipments()
    assert _history_ids() == ["shp_1", "shp_2"]
    assert list_trackers() == []


def test_a_purchase_reopens_a_batch_the_backfill_had_closed(client):
    """A sweep that saw the batch merely created marks it done. If it is then
    bought and the app closed with labels still queued, the next launch must
    look again."""
    unbought = _batch([_stub("shp_1", "created"), _stub("shp_2", "created")], "created")
    _saved_batch(unbought)
    client.batches_by_id["batch_1"] = unbought
    assert batches.backfill_batch_shipments() == 0  # closes it: nothing bought

    batches.save_batch_locally(QUEUED)  # the buy response, then the app closes
    client.batches_by_id["batch_1"] = SETTLED
    assert batches.backfill_batch_shipments() == 2


def test_backfill_skips_the_batch_the_page_is_already_following(client):
    _saved_batch(QUEUED)
    client.batches_by_id["batch_1"] = SETTLED
    assert batches.backfill_batch_shipments(skip_batch_id="batch_1") == 0
    assert client.retrieved == []


def test_one_unretrievable_batch_does_not_stop_the_backfill(client):
    _saved_batch(QUEUED, auto_track=True)
    _saved_batch(_batch([_stub("shp_3", "queued_for_purchase")], "created", "batch_2"))
    client.batches_by_id["batch_2"] = _batch(
        [_stub("shp_3", "postage_purchased", "EZ1000000003")], "purchased", "batch_2")
    # batch_1 is missing from the fake, so retrieving it raises KeyError.
    assert batches.backfill_batch_shipments() == 1
    assert _history_ids() == ["shp_3"]
