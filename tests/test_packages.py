from unittest.mock import Mock, patch

from app.core.db import init_db
from app.services.packages import (
    delete_saved_package,
    list_predefined_packages,
    list_saved_packages,
    package_code_from_choice,
    predefined_package_choices,
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

# Two different carriers exposing the SAME bare package code ("Letter" vs
# "LETTER") — the exact collision that made the flat dropdown ambiguous.
#
# Carrier codes here are LOWERCASE because that is what the live endpoint
# actually returns ("royalmailv3", not "RoyalMailV3"). Written CamelCase, this
# fixture passed against a display-name map that was itself keyed CamelCase and
# so proved nothing about real responses — every live lookup missed and every
# carrier fell through to its raw code. `human_readable` is likewise reproduced
# as the API sends it, including the unhelpful "RoyalMailV3".
COLLIDING_CARRIERS_RESPONSE = [
    {
        "name": "usps",
        "human_readable": "USPS",
        "predefined_packages": [
            {"carrier": "usps", "name": "Letter", "description": None,
             "dimensions": [], "max_weight": None},
        ],
    },
    {
        "name": "royalmailv3",
        "human_readable": "RoyalMailV3",
        "predefined_packages": [
            {"carrier": "royalmailv3", "name": "LETTER", "description": None,
             "dimensions": [], "max_weight": None},
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
    with patch("app.services.carriers.client_manager", mock_manager):
        list_predefined_packages()
    mock_manager.get_client.return_value.carrier_metadata.retrieve.assert_called_once_with(
        types=["predefined_packages"]
    )


def test_successful_fetch_parses_and_caches():
    mock_manager = _mock_client_manager(retrieve_return=SAMPLE_METADATA_RESPONSE)
    with patch("app.services.carriers.client_manager", mock_manager):
        packages = list_predefined_packages()

    assert len(packages) == 2
    names = {p.name for p in packages}
    assert names == {"FlatRateEnvelope", "SmallFlatRateBox"}
    assert all(p.carrier == "usps-test-fixture-unique" for p in packages)
    envelope = next(p for p in packages if p.name == "FlatRateEnvelope")
    assert envelope.dimensions == "12.5in x 9.5in"

    # A second call, even with the API failing, should now find these cached.
    mock_manager_failing = _mock_client_manager(retrieve_side_effect=ConnectionError("boom"))
    with patch("app.services.carriers.client_manager", mock_manager_failing):
        cached = list_predefined_packages()
    assert {p.name for p in cached} == {"FlatRateEnvelope", "SmallFlatRateBox"}


def test_refresh_replaces_stale_cache_rather_than_accumulating():
    mock_manager = _mock_client_manager(retrieve_return=SAMPLE_METADATA_RESPONSE)
    with patch("app.services.carriers.client_manager", mock_manager):
        list_predefined_packages()
        list_predefined_packages()

    with patch(
        "app.services.carriers.client_manager",
        _mock_client_manager(retrieve_side_effect=ConnectionError("boom")),
    ):
        cached = list_predefined_packages()
    # Two identical refreshes should not double up cached rows.
    assert len(cached) == 2


def test_network_failure_with_empty_cache_returns_empty_list():
    _clear_predefined_cache()
    mock_manager = _mock_client_manager(retrieve_side_effect=ConnectionError("boom"))
    with patch("app.services.carriers.client_manager", mock_manager):
        packages = list_predefined_packages()
    assert packages == []


def test_dropdown_choices_qualify_each_code_with_its_carrier():
    """Colliding bare codes stay distinguishable once carrier-qualified."""
    _clear_predefined_cache()
    mock_manager = _mock_client_manager(retrieve_return=COLLIDING_CARRIERS_RESPONSE)
    with patch("app.services.carriers.client_manager", mock_manager):
        choices = predefined_package_choices()

    # Both survive — one per carrier — instead of collapsing to a single
    # ambiguous "Letter". USPS is named from the API's own human_readable;
    # royalmailv3 comes through the override map, because the API's label for it
    # is just the code again.
    assert "USPS — Letter" in choices
    assert "Royal Mail V3 — LETTER" in choices
    # The raw lowercase code must not leak into a user-facing label.
    assert not any(c.startswith("royalmailv3") for c in choices)


def test_package_code_from_choice_round_trips():
    # A carrier-qualified label reduces to the bare code EasyPost wants...
    assert package_code_from_choice("Royal Mail — LETTER") == "LETTER"
    assert package_code_from_choice("USPS — Letter") == "Letter"
    # ...and a bare code typed straight into a CSV passes through unchanged.
    assert package_code_from_choice("FlatRateEnvelope") == "FlatRateEnvelope"
