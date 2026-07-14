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
