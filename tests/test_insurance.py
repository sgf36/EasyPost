"""Insurance amount validation, pending state, and local persistence.

The ceiling and the pending states are both taken from the live API rather than
assumed: 5000.00 is accepted and 5000.01 is rejected with "the amount provided
is greater than the maximum allowed", and EasyPost documents the status values
as new, pending, purchased, failed and cancelled.
"""

from unittest.mock import Mock, patch

import pytest
from easypost.easypost_object import convert_to_easypost_object

from app.core.db import db_cursor, init_db
from app.services.insurance import (
    INSURANCE_MAX_USD,
    InsuranceAmountError,
    is_pending,
    list_insurances,
    refund_insurance,
    retrieve_insurance,
    save_insurance_locally,
    validate_amount,
)


def setup_module(_module):
    init_db()


@pytest.fixture(autouse=True)
def _clean():
    with db_cursor() as cur:
        cur.execute("DELETE FROM insurances")
    yield


def _insurance(**over):
    payload = {
        "id": "ins_test", "object": "Insurance", "amount": "100.00",
        "status": "pending", "tracking_code": "EZ1000000001", "carrier": "USPS",
    }
    payload.update(over)
    return convert_to_easypost_object(payload)


def _manager():
    manager = Mock()
    manager.active_mode = "test"
    return manager


# ---------------------------------------------------------------------------
# Amount validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [("100", "100.00"), ("100.5", "100.50"), ("$250.00", "250.00"),
     ("1,250", "1250.00"), ("  99.99 ", "99.99"), (5000, "5000.00")],
)
def test_valid_amounts_are_normalised(given, expected):
    assert validate_amount(given) == expected


def test_the_documented_ceiling_is_exactly_five_thousand():
    assert INSURANCE_MAX_USD == 5000.0
    assert validate_amount("5000.00") == "5000.00"


@pytest.mark.parametrize("given", ["5000.01", "6000", "25000"])
def test_amounts_above_the_ceiling_are_refused_before_any_purchase(given):
    """EasyPost rejects these, but only at purchase time — after the user has
    already confirmed spending money."""
    with pytest.raises(InsuranceAmountError) as excinfo:
        validate_amount(given)
    assert "5,000" in str(excinfo.value)


@pytest.mark.parametrize("given", ["0", "-5", "", "abc", None])
def test_nonsense_amounts_are_refused(given):
    with pytest.raises(InsuranceAmountError):
        validate_amount(given)


# ---------------------------------------------------------------------------
# Pending state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["new", "pending", "NEW", "Pending"])
def test_unsettled_policies_are_pending(status):
    assert is_pending(_insurance(status=status))


@pytest.mark.parametrize("status", ["purchased", "failed", "cancelled"])
def test_settled_policies_are_not_pending(status):
    assert not is_pending(_insurance(status=status))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_a_policy_is_recorded_and_listed():
    with patch("app.services.insurance.client_manager", _manager()):
        save_insurance_locally(_insurance())
        records = list_insurances()
    assert len(records) == 1
    assert records[0].id == "ins_test"
    assert records[0].amount == "100.00"
    assert records[0].status == "pending"


def test_re_saving_updates_rather_than_duplicating():
    with patch("app.services.insurance.client_manager", _manager()):
        save_insurance_locally(_insurance())
        save_insurance_locally(_insurance(status="purchased"))
        records = list_insurances()
    assert len(records) == 1
    assert records[0].status == "purchased"


def test_pending_only_excludes_settled_policies():
    with patch("app.services.insurance.client_manager", _manager()):
        save_insurance_locally(_insurance(id="ins_a", status="pending"))
        save_insurance_locally(_insurance(id="ins_b", status="purchased"))
        save_insurance_locally(_insurance(id="ins_c", status="new"))
        pending = {r.id for r in list_insurances(pending_only=True)}
    assert pending == {"ins_a", "ins_c"}


def test_a_shipments_insured_amount_is_recorded_under_the_shipment_id():
    """`shipment.insure` returns the Shipment, which carries the declared value
    as `insurance` rather than `amount`."""
    shipment = convert_to_easypost_object(
        {"id": "shp_1", "object": "Shipment", "insurance": "75.00"}
    )
    with patch("app.services.insurance.client_manager", _manager()):
        save_insurance_locally(shipment, shipment_id="shp_1")
        records = list_insurances()
    assert records[0].amount == "75.00"
    assert records[0].shipment_id == "shp_1"


def test_retrieve_refreshes_the_local_record():
    manager = _manager()
    manager.get_client.return_value.insurance.retrieve.return_value = _insurance(
        status="purchased"
    )
    with patch("app.services.insurance.client_manager", manager):
        save_insurance_locally(_insurance(status="pending"))
        retrieve_insurance("ins_test")
        assert list_insurances()[0].status == "purchased"


def test_refund_records_the_cancellation():
    manager = _manager()
    manager.get_client.return_value.insurance.refund.return_value = _insurance(
        status="cancelled"
    )
    with patch("app.services.insurance.client_manager", manager):
        save_insurance_locally(_insurance(status="purchased"))
        refund_insurance("ins_test")
        assert list_insurances()[0].status == "cancelled"
