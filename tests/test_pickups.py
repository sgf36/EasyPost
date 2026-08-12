"""Pickup request shape.

The payload here is not a matter of taste. Verified against the live API:
sending a `shipments` array is rejected with "Invalid request, a batch with
shipments or a shipment is required to create a Pickup", and omitting
`instructions` is rejected with "pickup.instructions: instructions field is
required". The previous implementation did both, so scheduling a pickup could
never have succeeded.
"""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app.services.pickups import (
    DEFAULT_INSTRUCTIONS,
    PickupRequestError,
    create_pickup,
    ensure_offset,
)


def _manager():
    manager = Mock()
    manager.active_mode = "test"
    return manager


def _create(**over):
    params = dict(
        address_id="adr_1",
        shipment_ids=["shp_1"],
        min_datetime="2026-09-01T10:00:00",
        max_datetime="2026-09-01T16:00:00",
        instructions="Front desk",
        reference="ref-1",
    )
    params.update(over)
    manager = _manager()
    with patch("app.services.pickups.client_manager", manager):
        create_pickup(**params)
    return manager.get_client.return_value.pickup.create.call_args.kwargs


def test_one_shipment_is_sent_under_the_singular_key():
    kwargs = _create()
    assert kwargs["shipment"] == {"id": "shp_1"}
    # The array form is what EasyPost refuses outright.
    assert "shipments" not in kwargs


def test_several_shipments_are_refused_before_the_request_is_sent():
    """A pickup collects one shipment. Saying so plainly beats letting EasyPost
    answer with a generic semantic error."""
    with pytest.raises(PickupRequestError) as excinfo:
        _create(shipment_ids=["shp_1", "shp_2"])
    assert "batch" in str(excinfo.value)


def test_no_shipment_is_refused():
    with pytest.raises(PickupRequestError):
        _create(shipment_ids=[])


def test_instructions_are_always_sent():
    """EasyPost rejects a pickup with no instructions, and the old code sent
    None whenever the field was left blank."""
    kwargs = _create(instructions="")
    assert kwargs["instructions"] == DEFAULT_INSTRUCTIONS
    assert kwargs["instructions"]


def test_supplied_instructions_are_preserved():
    assert _create(instructions="  Ring the bell  ")["instructions"] == "Ring the bell"


def test_the_collection_window_carries_a_utc_offset():
    """A window without one leaves the carrier guessing which timezone 10:00
    refers to."""
    kwargs = _create()
    for key in ("min_datetime", "max_datetime"):
        parsed = datetime.fromisoformat(kwargs[key])
        assert parsed.tzinfo is not None, f"{key} has no offset: {kwargs[key]}"


def test_an_existing_offset_is_left_alone():
    assert ensure_offset("2026-09-01T10:00:00+05:30") == "2026-09-01T10:00:00+05:30"


def test_an_unparseable_datetime_is_passed_through_untouched():
    """Better to let EasyPost judge a value this cannot read than to mangle it
    into something different."""
    assert ensure_offset("next tuesday") == "next tuesday"


def test_the_address_is_sent_by_id():
    assert _create()["address"] == {"id": "adr_1"}
