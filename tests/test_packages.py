from unittest.mock import Mock, patch

from app.core.db import init_db
from app.services.packages import (
    delete_saved_package,
    list_predefined_packages,
    list_saved_packages,
    save_package,
)

SAMPLE_METADATA_RESPONSE = [
    {
        "name": "usps-test-fixture-unique",
        "human_readable": "USPS",
        "predefined_packages": [
            {
                "carrier": "usps-test-fixture-unique",
                "name": "FlatRateEnvelope",
                "description": None,
                "dimensions": ["12.5in x 9.5in"],
                "max_weight": None,
            },
            {
                "carrier": "usps-test-fixture-unique",
                "name": "SmallFlatRateBox",
                "description": None,
                "dimensions": ["8.6875in x 5.4375in x 1.75in"],
                "max_weight": None,
            },
        ],
    },
]


def setup_module(_module):
    init_db()


def test_saved_package_round_trip():
    save_package("Unique Test Box", 10, 8, 4, 32)
    saved = list_saved_packages()
    match = next(p for p in saved if p.name == "Unique Test Box")
    assert match.length == 10
    assert match.width == 8
    assert match.height == 4
    assert match.weight == 32

    delete_saved_package(match.id)
    saved_after = list_saved_packages()
    assert not any(p.name == "Unique Test Box" for p in saved_after)


def _mock_client_manager(retrieve_return=None, retrieve_side_effect=None):
    mock_client = Mock()
    if retrieve_side_effect is not None:
        mock_client.carrier_metadata.retrieve.side_effect = retrieve_side_effect
    else:
        mock_client.carrier_metadata.retrieve.return_value = retrieve_return
    mock_manager = Mock()
    mock_manager.get_client.return_value = mock_client
    return mock_manager


def _clear_predefined_cache():
    from app.core.db import db_cursor

    with db_cursor() as cur:
        cur.execute("DELETE FROM predefined_packages_cache")


def test_fetches_all_carriers_without_a_filter():
    """The whole point of the change: no carriers filter is sent, so the list
    reflects every carrier EasyPost supports, not one account's subset."""
    mock_manager = _mock_client_manager(retrieve_return=SAMPLE_METADATA_RESPONSE)
    with patch("app.services.packages.client_manager", mock_manager):
        list_predefined_packages()
    mock_manager.get_client.return_value.carrier_metadata.retrieve.assert_called_once_with(
        types=["predefined_packages"]
    )


def test_successful_fetch_parses_and_caches():
    mock_manager = _mock_client_manager(retrieve_return=SAMPLE_METADATA_RESPONSE)
    with patch("app.services.packages.client_manager", mock_manager):
        packages = list_predefined_packages()

    assert len(packages) == 2
    names = {p.name for p in packages}
    assert names == {"FlatRateEnvelope", "SmallFlatRateBox"}
    assert all(p.carrier == "usps-test-fixture-unique" for p in packages)
    envelope = next(p for p in packages if p.name == "FlatRateEnvelope")
    assert envelope.dimensions == "12.5in x 9.5in"

    # A second call, even with the API failing, should now find these cached.
    mock_manager_failing = _mock_client_manager(retrieve_side_effect=ConnectionError("boom"))
    with patch("app.services.packages.client_manager", mock_manager_failing):
        cached = list_predefined_packages()
    assert {p.name for p in cached} == {"FlatRateEnvelope", "SmallFlatRateBox"}


def test_refresh_replaces_stale_cache_rather_than_accumulating():
    mock_manager = _mock_client_manager(retrieve_return=SAMPLE_METADATA_RESPONSE)
    with patch("app.services.packages.client_manager", mock_manager):
        list_predefined_packages()
        list_predefined_packages()

    with patch(
        "app.services.packages.client_manager",
        _mock_client_manager(retrieve_side_effect=ConnectionError("boom")),
    ):
        cached = list_predefined_packages()
    # Two identical refreshes should not double up cached rows.
    assert len(cached) == 2


def test_network_failure_with_empty_cache_returns_empty_list():
    _clear_predefined_cache()
    mock_manager = _mock_client_manager(retrieve_side_effect=ConnectionError("boom"))
    with patch("app.services.packages.client_manager", mock_manager):
        packages = list_predefined_packages()
    assert packages == []
