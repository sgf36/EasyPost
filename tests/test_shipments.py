from unittest.mock import Mock, patch

from app.services.shipments import create_shipment

SAMPLE_CUSTOMS_INFO = {
    "contents_type": "merchandise",
    "restriction_type": "none",
    "non_delivery_option": "return",
    "customs_certify": True,
    "customs_signer": "Jane Doe",
    "customs_items": [
        {
            "description": "T-shirt",
            "quantity": 1,
            "value": 20.0,
            "weight": 8,
            "hs_tariff_number": "6109.10.00",
            "origin_country": "US",
            "currency": "USD",
        }
    ],
    "eel_pfc": "NOEEI 30.37(a)",
}


def _mock_client_manager():
    mock_client = Mock()
    mock_client.shipment.create.return_value = Mock()
    mock_manager = Mock()
    mock_manager.get_client.return_value = mock_client
    return mock_manager, mock_client


def test_domestic_shipment_omits_customs_info():
    mock_manager, mock_client = _mock_client_manager()
    with patch("app.services.shipments.client_manager", mock_manager):
        create_shipment(
            to_address_id="addr_to",
            from_address_id="addr_from",
            length=6,
            width=6,
            height=6,
            weight=16,
        )

    _, kwargs = mock_client.shipment.create.call_args
    assert "customs_info" not in kwargs
    assert kwargs["parcel"] == {"weight": 16, "length": 6, "width": 6, "height": 6}


def test_predefined_package_omits_manual_dimensions():
    mock_manager, mock_client = _mock_client_manager()
    with patch("app.services.shipments.client_manager", mock_manager):
        create_shipment(
            to_address_id="addr_to",
            from_address_id="addr_from",
            weight=16,
            predefined_package="FedExPak",
        )

    _, kwargs = mock_client.shipment.create.call_args
    assert kwargs["parcel"] == {"weight": 16, "predefined_package": "FedExPak"}


def test_international_shipment_includes_customs_info():
    mock_manager, mock_client = _mock_client_manager()
    with patch("app.services.shipments.client_manager", mock_manager):
        create_shipment(
            to_address_id="addr_to",
            from_address_id="addr_from",
            length=6,
            width=6,
            height=6,
            weight=16,
            customs_info=SAMPLE_CUSTOMS_INFO,
        )

    _, kwargs = mock_client.shipment.create.call_args
    assert kwargs["customs_info"] == SAMPLE_CUSTOMS_INFO
    assert kwargs["customs_info"]["customs_items"][0]["origin_country"] == "US"


def test_falsy_customs_info_is_omitted():
    mock_manager, mock_client = _mock_client_manager()
    with patch("app.services.shipments.client_manager", mock_manager):
        create_shipment(
            to_address_id="addr_to",
            from_address_id="addr_from",
            length=6,
            width=6,
            height=6,
            weight=16,
            customs_info=None,
        )

    _, kwargs = mock_client.shipment.create.call_args
    assert "customs_info" not in kwargs
