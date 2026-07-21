from unittest.mock import Mock, patch

from app.core.db import init_db
from app.services.hts_lookup import search_hts_codes

SAMPLE_RESPONSE = [
    {
        "htsno": "7403.11.00.00",
        "description": "Refined copper cathodes",
        "general": "1%",
        "special": "Free (A,AU,BH...)",
        "other": "6%",
        "units": ["kg"],
        "indent": "2",
    },
    {
        "htsno": "",
        "description": "Copper and articles thereof:",
        "general": "",
        "special": "",
        "other": "",
        "units": [],
        "indent": "0",
    },
]


def setup_module(_module):
    init_db()


def test_successful_search_parses_and_caches(monkeypatch):
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = SAMPLE_RESPONSE

    with patch("app.services.hts_lookup.requests.get", return_value=mock_response) as mock_get:
        results = search_hts_codes("copper-unique-test-1")

    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["params"] == {"keyword": "copper-unique-test-1"}
    assert len(results) == 2
    assert results[0].htsno == "7403.11.00.00"
    assert results[0].description == "Refined copper cathodes"
    assert results[0].general_rate == "1%"
    assert results[0].units == "kg"
    assert results[0].indent == 2
    assert results[0].from_cache is False
    # Blank htsno (a category header row) is preserved, not dropped.
    assert results[1].htsno == ""

    # The rows were cached: a later search that cannot reach the live API falls
    # back to them. Searched by a term present in the cached description, since
    # the cache matches on htsno/description rather than the original keyword.
    # (Mocked deliberately — a unit test must never depend on the live USITC API.)
    with patch("app.services.hts_lookup.requests.get", side_effect=ConnectionError("offline")):
        cached = search_hts_codes("Refined copper")
    assert any(r.htsno == "7403.11.00.00" for r in cached)
    assert all(r.from_cache for r in cached)


def test_empty_keyword_returns_empty_without_network_call():
    with patch("app.services.hts_lookup.requests.get") as mock_get:
        results = search_hts_codes("   ")
    mock_get.assert_not_called()
    assert results == []


def test_network_failure_falls_back_to_cache(monkeypatch):
    # Prime the cache with a successful search first.
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = [
        {
            "htsno": "9999.99.99.99",
            "description": "fallback-fixture-widget",
            "general": "Free",
            "special": "",
            "other": "",
            "units": "No.",
            "indent": "1",
        }
    ]
    with patch("app.services.hts_lookup.requests.get", return_value=mock_response):
        search_hts_codes("fallback-fixture-widget")

    # Now simulate the live API being unreachable.
    with patch("app.services.hts_lookup.requests.get", side_effect=ConnectionError("boom")):
        results = search_hts_codes("fallback-fixture-widget")

    assert len(results) == 1
    assert results[0].htsno == "9999.99.99.99"
    assert results[0].from_cache is True


def test_non_200_response_falls_back_to_cache():
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = Exception("500 server error")

    with patch("app.services.hts_lookup.requests.get", return_value=mock_response):
        results = search_hts_codes("no-such-fixture-should-be-empty")

    # No prior cache entry for this keyword, so an empty list, not a crash.
    assert results == []


def test_unexpected_response_shape_falls_back_gracefully():
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {"not": "a list"}

    with patch("app.services.hts_lookup.requests.get", return_value=mock_response):
        results = search_hts_codes("no-such-fixture-either")

    assert results == []
