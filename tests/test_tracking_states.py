"""Tracker terminal states, status_detail, and the auto-track hand-off."""

from unittest.mock import Mock, patch

import pytest
from easypost.easypost_object import convert_to_easypost_object

from app.core.db import db_cursor, init_db
from app.services.tracking import (
    is_problem,
    is_terminal,
    list_trackers,
    refresh_all_trackers,
    save_tracker_locally,
    track_shipment,
)


def setup_module(_module):
    init_db()


@pytest.fixture(autouse=True)
def _clean():
    with db_cursor() as cur:
        cur.execute("DELETE FROM trackers")
    yield


def _manager():
    manager = Mock()
    manager.active_mode = "test"
    return manager


def _tracker(**over):
    payload = {
        "id": "trk_1", "object": "Tracker", "tracking_code": "EZ1000000001",
        "carrier": "USPS", "status": "in_transit",
    }
    payload.update(over)
    return convert_to_easypost_object(payload)


@pytest.mark.parametrize(
    "status", ["delivered", "return_to_sender", "failure", "cancelled", "error"]
)
def test_terminal_statuses_are_recognised(status):
    assert is_terminal(status)


@pytest.mark.parametrize(
    "status", ["pre_transit", "in_transit", "out_for_delivery",
               "available_for_pickup", "unknown"]
)
def test_in_flight_statuses_are_not_terminal(status):
    assert not is_terminal(status)


@pytest.mark.parametrize("status", ["return_to_sender", "failure", "error"])
def test_problem_statuses_are_flagged(status):
    assert is_problem(status)


def test_delivered_is_terminal_but_not_a_problem():
    assert is_terminal("delivered") and not is_problem("delivered")


def test_status_detail_is_stored():
    """A bare "failure" says a parcel is stuck without saying why."""
    with patch("app.services.tracking.client_manager", _manager()):
        save_tracker_locally(_tracker(status="failure", status_detail="address_incorrect"))
        record = list_trackers()[0]
    assert record.status == "failure"
    assert record.status_detail == "address_incorrect"


def test_status_detail_is_updated_on_refresh():
    with patch("app.services.tracking.client_manager", _manager()):
        save_tracker_locally(_tracker(status="in_transit"))
        save_tracker_locally(_tracker(status="failure", status_detail="damaged"))
        record = list_trackers()[0]
    assert record.status_detail == "damaged"


def test_terminal_trackers_are_not_re_polled():
    """A delivered parcel will not change, and on a long history re-reading
    settled trackers is the bulk of every refresh."""
    manager = _manager()
    manager.get_client.return_value.tracker.retrieve.side_effect = (
        lambda tid: _tracker(id=tid)
    )
    with patch("app.services.tracking.client_manager", manager):
        save_tracker_locally(_tracker(id="trk_open", status="in_transit"))
        save_tracker_locally(_tracker(id="trk_done", status="delivered"))
        refreshed = refresh_all_trackers()

    assert len(refreshed) == 1
    called = [c.args[0] for c in manager.get_client.return_value.tracker.retrieve.call_args_list]
    assert called == ["trk_open"]


def test_terminal_trackers_can_be_forced():
    manager = _manager()
    manager.get_client.return_value.tracker.retrieve.side_effect = (
        lambda tid: _tracker(id=tid)
    )
    with patch("app.services.tracking.client_manager", manager):
        save_tracker_locally(_tracker(id="trk_done", status="delivered"))
        assert len(refresh_all_trackers(include_terminal=True)) == 1


def test_one_unreadable_tracker_does_not_abort_the_refresh():
    """Previously a single deleted or malformed tracker made the whole Tracking
    page impossible to update."""
    manager = _manager()

    def _retrieve(tid):
        if tid == "trk_bad":
            raise RuntimeError("404 not found")
        return _tracker(id=tid)

    manager.get_client.return_value.tracker.retrieve.side_effect = _retrieve
    with patch("app.services.tracking.client_manager", manager):
        save_tracker_locally(_tracker(id="trk_bad", status="in_transit"))
        save_tracker_locally(_tracker(id="trk_ok", status="in_transit"))
        refreshed = refresh_all_trackers()
    assert [t.id for t in refreshed] == ["trk_ok"]


# ---------------------------------------------------------------------------
# Auto-tracking a bought label
# ---------------------------------------------------------------------------


def test_a_bought_shipment_is_added_to_tracking():
    shipment = convert_to_easypost_object({
        "id": "shp_1", "object": "Shipment", "tracking_code": "EZ1000000001",
        "tracker": {"id": "trk_new", "object": "Tracker", "status": "pre_transit"},
    })
    with patch("app.services.tracking.client_manager", _manager()):
        assert track_shipment(shipment) is True
        assert [r.id for r in list_trackers()] == ["trk_new"]


def test_a_shipment_without_a_tracker_is_skipped():
    shipment = convert_to_easypost_object({"id": "shp_1", "object": "Shipment"})
    with patch("app.services.tracking.client_manager", _manager()):
        assert track_shipment(shipment) is False


def test_a_tracking_failure_never_propagates_to_the_purchase():
    """The label is already paid for by this point."""
    shipment = convert_to_easypost_object({
        "id": "shp_1", "object": "Shipment",
        "tracker": {"id": "trk_new", "object": "Tracker"},
    })
    with patch("app.services.tracking.save_tracker_locally",
               side_effect=RuntimeError("database is locked")):
        assert track_shipment(shipment) is False  # does not raise
