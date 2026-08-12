from app.core.errors import format_api_error


class _FakeApiError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


def test_prefers_detailed_errors_list_over_generic_message():
    exc = _FakeApiError(
        "The request could not be understood by the server due to malformed syntax.",
        errors=[{"message": "From address error: missing required customs address data: name of person or company"}],
    )
    assert format_api_error(exc) == (
        "From address error: missing required customs address data: name of person or company"
    )


def test_joins_multiple_detailed_error_messages():
    exc = _FakeApiError(
        "generic",
        errors=[{"message": "first problem"}, {"message": "second problem"}],
    )
    assert format_api_error(exc) == "first problem; second problem"


def test_falls_back_to_str_when_no_errors_attribute():
    assert format_api_error(ValueError("plain failure")) == "plain failure"


def test_falls_back_to_str_when_errors_list_is_empty():
    exc = _FakeApiError("generic message", errors=[])
    assert format_api_error(exc) == str(exc)


def test_falls_back_to_str_when_error_entries_have_no_message_key():
    exc = _FakeApiError("generic message", errors=[{"code": "SOMETHING"}])
    assert format_api_error(exc) == str(exc)


# ---------------------------------------------------------------------------
# Shapes EasyPost actually returns, beyond the simple {"message": ...} case
# ---------------------------------------------------------------------------

from easypost.easypost_object import convert_to_easypost_object  # noqa: E402

from app.core.errors import carrier_messages  # noqa: E402


def test_bare_string_sub_errors_are_surfaced():
    """An entry in `errors` is sometimes a plain string rather than an object.
    Reading only e["message"] dropped these silently."""
    exc = _FakeApiError("generic", errors=["parcel too heavy", "invalid postcode"])
    assert format_api_error(exc) == "parcel too heavy; invalid postcode"


def test_nested_errors_are_followed_to_the_specific_reason():
    exc = _FakeApiError(
        "generic",
        errors=[{"field": "shipment", "message": "Invalid request",
                 "errors": [{"field": "to_address.zip", "message": "must be present"}]}],
    )
    assert format_api_error(exc) == "to_address.zip: must be present"


def test_field_names_are_included_when_present():
    exc = _FakeApiError("generic", errors=[{"field": "parcel.weight", "message": "required"}])
    assert format_api_error(exc) == "parcel.weight: required"


def test_a_repeated_cause_reads_once():
    exc = _FakeApiError("generic", errors=[{"message": "same"}, {"message": "same"}])
    assert format_api_error(exc) == "same"


def test_easypost_objects_are_read_correctly():
    """`errors` entries arrive as EasyPostObject, which implements .get() but is
    NOT a dict — an isinstance(dict) test is False for every real response."""
    entries = convert_to_easypost_object([{"field": "to_address", "message": "is invalid"}])
    assert not isinstance(entries[0], dict)
    exc = _FakeApiError("generic", errors=entries)
    assert format_api_error(exc) == "to_address: is invalid"


def test_mixed_string_and_object_entries():
    exc = _FakeApiError("generic", errors=["plain text", {"message": "structured"}])
    assert format_api_error(exc) == "plain text; structured"


# --- carrier messages ------------------------------------------------------


def test_carrier_messages_explain_a_missing_carrier():
    shipment = convert_to_easypost_object({
        "id": "shp_1", "object": "Shipment",
        "messages": [
            {"carrier": "USPS", "type": "rate_error", "message": "Unable to retrieve rates"},
            {"carrier": "FedEx", "type": "rate_error", "message": "Dimensions exceed maximum"},
        ],
    })
    assert carrier_messages(shipment) == [
        "USPS: Unable to retrieve rates",
        "FedEx: Dimensions exceed maximum",
    ]


def test_a_shipment_without_messages_yields_nothing():
    shipment = convert_to_easypost_object({"id": "shp_1", "object": "Shipment"})
    assert carrier_messages(shipment) == []
