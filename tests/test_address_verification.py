"""Address verification failure detection.

Every object in these tests is built with the SDK's own
``convert_to_easypost_object`` rather than a plain dict. That is the whole
point: the original bug was an ``isinstance(verifications, dict)`` guard, and
``EasyPostObject`` does not subclass ``dict``. A test written with plain dicts
passes against the broken code and proves nothing.
"""

import pytest
from easypost.easypost_object import convert_to_easypost_object

from app.services.addresses import (
    AddressVerificationError,
    address_is_verified,
    verify_address,
)


def _address(verifications=None):
    payload = {"id": "adr_test", "object": "Address", "street1": "1 Test St"}
    if verifications is not None:
        payload["verifications"] = verifications
    return convert_to_easypost_object(payload)


def _patch_client(monkeypatch, address):
    class _Client:
        class address:  # noqa: N801 - mirrors the SDK's attribute name
            @staticmethod
            def create(**_kwargs):
                return address

    monkeypatch.setattr(
        "app.services.addresses.client_manager",
        type("M", (), {"get_client": staticmethod(lambda: _Client)})(),
    )


def _verify():
    return verify_address(street1="1 Test St", city="London", state="", zip="SW1A 2AA", country="GB")


def test_easypost_object_is_not_a_dict():
    """The premise of the bug — guard against it ever being 'fixed' back."""
    verifications = _address({"delivery": {"success": True}}).verifications
    assert not isinstance(verifications, dict)
    assert verifications.get("delivery").get("success") is True


def test_failed_verification_raises(monkeypatch):
    _patch_client(
        monkeypatch,
        _address({"delivery": {"success": False, "errors": [{"message": "Address not found"}]}}),
    )
    with pytest.raises(AddressVerificationError) as excinfo:
        _verify()
    assert "Address not found" in excinfo.value.messages


def test_error_suggestion_is_included(monkeypatch):
    _patch_client(
        monkeypatch,
        _address(
            {
                "delivery": {
                    "success": False,
                    "errors": [{"message": "House number is invalid", "suggestion": "try 1"}],
                }
            }
        ),
    )
    with pytest.raises(AddressVerificationError) as excinfo:
        _verify()
    assert excinfo.value.messages == ["House number is invalid (try 1)"]


def test_successful_verification_returns_address(monkeypatch):
    _patch_client(monkeypatch, _address({"delivery": {"success": True}}))
    address = _verify()
    assert address.id == "adr_test"
    assert address_is_verified(address) is True


def test_absent_verification_is_not_reported_as_verified(monkeypatch):
    """No delivery verification means "not checked", which must never be
    presented to the user as "deliverable" — but it is not an error either."""
    _patch_client(monkeypatch, _address(None))
    address = _verify()  # does not raise
    assert address_is_verified(address) is False


def test_success_must_be_exactly_true(monkeypatch):
    """A missing `success` key previously defaulted to True (fail-open)."""
    _patch_client(monkeypatch, _address({"delivery": {"errors": []}}))
    with pytest.raises(AddressVerificationError):
        _verify()
