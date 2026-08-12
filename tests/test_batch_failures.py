"""Per-shipment batch failures, and the tracking hand-off after a purchase.

Batches are built with the SDK's own ``convert_to_easypost_object`` throughout.
``EasyPostObject`` implements ``.get()`` but does not subclass ``dict``, so code
that reaches into a batch has to be written against the real type — a plain-dict
fixture can pass against code that would fail on a live response.
"""

from unittest.mock import Mock, patch

from easypost.easypost_object import convert_to_easypost_object

from app.services.batches import (
    batch_failure_messages,
    batch_label_urls,
    bought_shipment_ids,
    track_batch_shipments,
)

# The exact message a missing signature option produces: the batch is created
# successfully and only the purchase reports the problem.
SIGNATURE_MESSAGE = (
    "RoyalMailV3 does not offer service RoyalMail2ndClassSignedFor for this shipment."
)


def _batch(shipments, state="purchase_failed"):
    return convert_to_easypost_object(
        {"id": "batch_1", "object": "Batch", "state": state, "shipments": shipments}
    )


def test_no_failures_reports_nothing():
    batch = _batch([
        {"id": "shp_1", "batch_status": "postage_purchased", "tracking_code": "TT1GB"},
    ], state="purchased")
    assert batch_failure_messages(batch) == []


def test_purchase_failure_message_is_surfaced():
    """Without this the user sees a state change and no explanation — which is
    precisely how the original "buy all shipments" failure presented."""
    batch = _batch([
        {"id": "shp_1", "batch_status": "postage_purchase_failed",
         "batch_message": SIGNATURE_MESSAGE, "reference": "order-1"},
    ])
    messages = batch_failure_messages(batch)
    assert len(messages) == 1
    assert SIGNATURE_MESSAGE in messages[0]
    assert messages[0].startswith("order-1")


def test_creation_failure_is_surfaced_too():
    batch = _batch([
        {"id": "shp_1", "batch_status": "creation_failed", "batch_message": "Invalid address"},
    ], state="creation_failed")
    assert "Invalid address" in batch_failure_messages(batch)[0]


def test_one_repeated_cause_reads_as_one_line():
    """A 200-row batch failing for one reason must not produce 200 identical
    lines in a dialog."""
    batch = _batch([
        {"id": f"shp_{i}", "batch_status": "postage_purchase_failed",
         "batch_message": SIGNATURE_MESSAGE, "reference": f"order-{i}"}
        for i in range(200)
    ])
    assert len(batch_failure_messages(batch)) == 1


def test_distinct_causes_are_all_reported():
    batch = _batch([
        {"id": "shp_1", "batch_status": "postage_purchase_failed", "batch_message": "Too heavy"},
        {"id": "shp_2", "batch_status": "postage_purchase_failed", "batch_message": "Bad postcode"},
    ])
    assert len(batch_failure_messages(batch)) == 2


def test_successful_shipments_are_not_reported_as_failures():
    batch = _batch([
        {"id": "shp_1", "batch_status": "postage_purchased", "tracking_code": "TT1GB"},
        {"id": "shp_2", "batch_status": "postage_purchase_failed", "batch_message": "Too heavy"},
    ])
    assert len(batch_failure_messages(batch)) == 1


def test_a_batch_with_no_shipments_is_handled():
    assert batch_failure_messages(convert_to_easypost_object({"id": "b", "state": "creating"})) == []


# ---------------------------------------------------------------------------
# Tracking hand-off
# ---------------------------------------------------------------------------


# A purchased batch, shaped exactly as EasyPost returns one: the embedded
# shipments are STUBS. Verified against a real batch, each carries only id,
# reference, tracking_code, batch_status and batch_message — no postage_label
# and no tracker. Anything needing those has to retrieve the shipment itself.
BOUGHT_BATCH = _batch([
    {"id": "shp_1", "batch_status": "postage_purchased", "tracking_code": "TT1GB",
     "reference": "order-1"},
    {"id": "shp_2", "batch_status": "postage_purchased", "tracking_code": "TT2GB",
     "reference": "order-2"},
], state="purchased")


def _retrieving_manager(by_id):
    client = Mock()
    client.shipment.retrieve.side_effect = lambda sid: convert_to_easypost_object(by_id[sid])
    manager = Mock()
    manager.get_client.return_value = client
    return manager, client


def _full(shipment_id, *, label_url=None, tracker_id=None):
    payload = {"id": shipment_id, "object": "Shipment"}
    if label_url:
        payload["postage_label"] = {"id": "pl_1", "label_url": label_url}
    if tracker_id:
        payload["tracker"] = {"id": tracker_id, "object": "Tracker"}
    return payload


def test_only_shipments_that_bought_postage_are_followed_up():
    batch = _batch([
        {"id": "shp_1", "batch_status": "postage_purchased", "tracking_code": "TT1GB"},
        {"id": "shp_2", "batch_status": "postage_purchase_failed", "batch_message": "nope"},
    ])
    assert bought_shipment_ids(batch) == ["shp_1"]


def test_bought_shipments_are_recorded_for_tracking():
    manager, client = _retrieving_manager({
        "shp_1": _full("shp_1", tracker_id="trk_1"),
        "shp_2": _full("shp_2", tracker_id="trk_2"),
    })
    saved = []
    with patch("app.services.batches.client_manager", manager), \
            patch("app.services.tracking.save_tracker_locally", side_effect=saved.append):
        assert track_batch_shipments(BOUGHT_BATCH) == 2
    assert [t.id for t in saved] == ["trk_1", "trk_2"]


def test_the_tracker_is_taken_from_the_shipment_not_created_afresh():
    """Buying the label already created the tracker, and creating another is
    rejected outright in test mode, where EasyPost accepts only its own
    EZ-prefixed test tracking numbers."""
    manager, client = _retrieving_manager({
        "shp_1": _full("shp_1", tracker_id="trk_1"),
        "shp_2": _full("shp_2", tracker_id="trk_2"),
    })
    with patch("app.services.batches.client_manager", manager), \
            patch("app.services.tracking.save_tracker_locally"):
        track_batch_shipments(BOUGHT_BATCH)
    client.tracker.create.assert_not_called()


def test_a_tracking_failure_never_fails_the_purchase():
    """The labels are already paid for by this point. Local bookkeeping that
    goes wrong must not be reported to the user as a failed purchase."""
    manager, _ = _retrieving_manager({
        "shp_1": _full("shp_1", tracker_id="trk_1"),
        "shp_2": _full("shp_2", tracker_id="trk_2"),
    })

    def _explode_on_first(tracker):
        if tracker.id == "trk_1":
            raise RuntimeError("database is locked")

    with patch("app.services.batches.client_manager", manager), \
            patch("app.services.tracking.save_tracker_locally", side_effect=_explode_on_first):
        # Does not raise, and the second shipment is still recorded.
        assert track_batch_shipments(BOUGHT_BATCH) == 1


def test_an_unretrievable_shipment_does_not_stop_the_others():
    client = Mock()

    def _retrieve(sid):
        if sid == "shp_1":
            raise RuntimeError("500 from EasyPost")
        return convert_to_easypost_object(_full(sid, tracker_id="trk_2"))

    client.shipment.retrieve.side_effect = _retrieve
    manager = Mock()
    manager.get_client.return_value = client
    with patch("app.services.batches.client_manager", manager), \
            patch("app.services.tracking.save_tracker_locally"):
        assert track_batch_shipments(BOUGHT_BATCH) == 1


# ---------------------------------------------------------------------------
# Label URLs for the print sheet
# ---------------------------------------------------------------------------


def test_label_urls_come_from_retrieved_shipments():
    """The batch's own shipment stubs carry no postage_label at all, so reading
    them returned nothing every time and left the print-sheet export
    permanently disabled after a batch purchase."""
    manager, _ = _retrieving_manager({
        "shp_1": _full("shp_1", label_url="https://example.test/1.png"),
        "shp_2": _full("shp_2", label_url="https://example.test/2.png"),
    })
    with patch("app.services.batches.client_manager", manager):
        urls = batch_label_urls(BOUGHT_BATCH)
    assert urls == ["https://example.test/1.png", "https://example.test/2.png"]


def test_a_shipment_without_a_label_is_simply_omitted():
    manager, _ = _retrieving_manager({
        "shp_1": _full("shp_1", label_url="https://example.test/1.png"),
        "shp_2": _full("shp_2"),
    })
    with patch("app.services.batches.client_manager", manager):
        assert batch_label_urls(BOUGHT_BATCH) == ["https://example.test/1.png"]
