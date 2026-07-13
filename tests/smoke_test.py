"""End-to-end smoke test against EasyPost's real test-mode API.

Test mode never charges a real carrier, so this is safe to run against your
own account. Requires an environment variable with your EasyPost *test* key:

    EASYPOST_TEST_API_KEY=test_xxx pytest tests/smoke_test.py -v

Skipped automatically if that variable isn't set (e.g. in CI).
"""

import os

import pytest

import easypost

API_KEY = os.environ.get("EASYPOST_TEST_API_KEY")

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="Set EASYPOST_TEST_API_KEY to run the live test-mode smoke test."
)


@pytest.fixture(scope="module")
def client():
    return easypost.EasyPostClient(API_KEY)


def test_create_rate_buy_track_refund(client):
    from_address = client.address.create(
        verify_strict=True,
        name="EasyPost Desktop Smoke Test",
        street1="417 Montgomery Street",
        street2="5th Floor",
        city="San Francisco",
        state="CA",
        zip="94104",
        country="US",
        phone="4155555555",
    )
    to_address = client.address.create(
        verify_strict=True,
        name="Jane Doe",
        street1="179 N Harbor Dr",
        city="Redondo Beach",
        state="CA",
        zip="90277",
        country="US",
        phone="4155555555",
    )

    shipment = client.shipment.create(
        to_address={"id": to_address.id},
        from_address={"id": from_address.id},
        parcel={"length": 10, "width": 6, "height": 4, "weight": 16},
    )
    assert shipment.rates, "expected at least one rate in test mode"

    lowest_rate = min(shipment.rates, key=lambda r: float(r.rate))
    bought = client.shipment.buy(shipment.id, rate={"id": lowest_rate.id})
    assert bought.postage_label is not None
    assert bought.tracking_code

    tracker = client.tracker.create(tracking_code=bought.tracking_code)
    assert tracker.id

    refunded = client.shipment.refund(bought.id)
    assert refunded.refund_status in ("submitted", "refunded")
