"""Rating one row to narrow the batch carrier and service list.

A batch is never rated, so its carrier and service must be named before
anything is priced — chosen blind from a catalogue where Royal Mail alone
publishes 243 services. Naming one the route does not support creates a batch
that cannot be bought, which is the expensive way to find out.

Rating a single representative row answers the question first. The value of the
test below is mostly in what it pins about the *shape* of that request: it must
be the payload the batch will send, minus the carrier and service.
"""

import pytest

from app.services import batches
from app.services.batches import _validate_row, quoted_services, rate_representative_row


class _Rate:
    def __init__(self, carrier, service, rate=None, currency=None):
        self.carrier, self.service, self.rate, self.currency = carrier, service, rate, currency


class _Shipment:
    def __init__(self, rates):
        self.rates = rates


class _FakeClient:
    def __init__(self):
        self.created = None
        self.shipment = self

    def create(self, **params):
        self.created = params
        return _Shipment([_Rate("RoyalMailV3", "InternationalStandardOnAccount", "8.35", "GBP")])


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(batches.client_manager, "get_client", lambda: client)
    return client


def _row(**overrides):
    fields = {
        "to_name": "K Fields", "to_street1": "1 Sunset", "to_city": "Los Angeles",
        "to_state": "CA", "to_zip": "90001", "to_country": "US", "weight": "3.5",
        "predefined_package": "Letter", "customs_description": "T-shirt",
        "customs_quantity": "1", "customs_value": "12.50",
    }
    fields.update(overrides)
    return _validate_row(2, fields, from_country="GB")


DECLARATION = {"customs_signer": "S Fields", "contents_type": "merchandise"}


def test_rating_omits_carrier_and_service(fake_client):
    """Naming them is what narrows EasyPost's reply to that one service, which
    would defeat the entire point of asking."""
    rate_representative_row("adr_1", _row(), from_country="GB", declaration=DECLARATION)
    assert "carrier" not in fake_client.created
    assert "service" not in fake_client.created


def test_rating_sends_the_same_payload_the_batch_will(fake_client):
    """A differently-shaped request answers a question nobody asked."""
    rate_representative_row("adr_1", _row(), from_country="GB", declaration=DECLARATION)
    batch_params = batches._row_to_shipment_params(
        _row(), "adr_1", from_country="GB", declaration=DECLARATION
    )
    for field in ("to_address", "parcel", "from_address", "customs_info"):
        assert fake_client.created[field] == batch_params[field]


def test_international_rating_carries_the_declaration(fake_client):
    """Rating without customs would quote services that then refuse the label."""
    rate_representative_row("adr_1", _row(), from_country="GB", declaration=DECLARATION)
    assert fake_client.created["customs_info"]["customs_items"][0]["description"] == "T-shirt"


def test_domestic_rating_carries_no_declaration(fake_client):
    row = _validate_row(2, {
        "to_street1": "1 Main", "to_city": "Boston", "to_zip": "02110",
        "to_country": "US", "weight": "16", "predefined_package": "Letter",
    }, from_country="US")
    rate_representative_row("adr_1", row, from_country="US", declaration=None)
    assert "customs_info" not in fake_client.created


def test_quoted_services_flattens_the_rates(fake_client):
    shipment = rate_representative_row("adr_1", _row(), from_country="GB", declaration=DECLARATION)
    quotes = quoted_services(shipment)
    assert quotes == [{
        "carrier": "RoyalMailV3",
        "service": "InternationalStandardOnAccount",
        "rate": "8.35",
        "currency": "GBP",
    }]


def test_quoted_services_skips_incomplete_rates():
    """A rate missing either half cannot be matched against the catalogue, and
    a half-named entry would filter the list down to nothing."""
    assert quoted_services(_Shipment([_Rate("RoyalMailV3", None), _Rate(None, "X")])) == []


def test_quoted_services_tolerates_an_unrated_shipment():
    assert quoted_services(_Shipment(None)) == []
