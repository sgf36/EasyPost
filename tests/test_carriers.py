"""Carrier catalogue: display names and the service-level list.

Fixtures here mirror the live endpoint's actual shape — carrier codes in
LOWERCASE, `human_readable` sometimes a real name ("Royal Mail") and sometimes
just the code echoed back ("RoyalMailV3"). Both were captured from real
responses; a fixture written in CamelCase would pass against the very bug this
covers.
"""

from unittest.mock import Mock, patch

import pytest

from app.core.db import db_cursor, init_db
from app.services import carriers as C

# Trimmed from a real carrier_metadata response.
METADATA = [
    {
        "name": "royalmailv3",
        # The API's own label for this carrier is the code again — which is why
        # an override map is still needed even with human_readable available.
        "human_readable": "RoyalMailV3",
        "service_levels": [
            {"carrier": "royalmailv3", "name": "RoyalMail2ndClassSignedFor",
             "human_readable": "RoyalMail2ndClassSignedFor",
             "dimensions": ["13.9in x 9.84in x 0.98in"], "max_weight": 26.46},
            {"carrier": "royalmailv3", "name": "RoyalMail1stClass",
             "human_readable": "RoyalMail1stClass", "dimensions": [], "max_weight": None},
        ],
    },
    {
        "name": "usps",
        "human_readable": "USPS",
        "service_levels": [
            {"carrier": "usps", "name": "First", "human_readable": "First",
             "dimensions": [], "max_weight": None},
        ],
    },
]


def setup_module(_module):
    init_db()


@pytest.fixture(autouse=True)
def _clean_caches():
    with db_cursor() as cur:
        cur.execute("DELETE FROM service_levels_cache")
        cur.execute("DELETE FROM carriers_cache")
    C._carrier_names = None
    yield


def _manager(return_value=None, side_effect=None):
    client = Mock()
    if side_effect is not None:
        client.carrier_metadata.retrieve.side_effect = side_effect
    else:
        client.carrier_metadata.retrieve.return_value = return_value
    manager = Mock()
    manager.get_client.return_value = client
    return manager


# ---------------------------------------------------------------------------
# Display names
# ---------------------------------------------------------------------------


def test_lowercase_api_codes_resolve_to_a_name():
    """The original bug: the map was keyed CamelCase while the API returns
    lowercase, so every lookup fell through to the raw code."""
    with patch("app.services.carriers.client_manager", _manager(METADATA)):
        C.list_service_levels()

    assert C.carrier_display_name("usps") == "USPS"
    assert C.carrier_display_name("royalmailv3") == "Royal Mail V3"


def test_camelcase_code_from_a_rate_resolves_to_the_same_name():
    """`rate.carrier` is CamelCase while the catalogue is lowercase; both are
    spellings of one carrier and must not produce two different labels."""
    with patch("app.services.carriers.client_manager", _manager(METADATA)):
        C.list_service_levels()

    assert C.carrier_display_name("USPS") == C.carrier_display_name("usps")
    assert C.carrier_display_name("RoyalMailV3") == "Royal Mail V3"


def test_royalmail_and_royalmailv3_are_labelled_distinctly():
    """They are separate carriers with different catalogues, and choosing the
    wrong one fails at purchase — so they must not collapse to one label."""
    assert C.carrier_display_name("royalmail") != C.carrier_display_name("royalmailv3")


def test_unknown_carrier_falls_back_to_its_code():
    assert C.carrier_display_name("someneverseencarrier") == "someneverseencarrier"
    assert C.carrier_display_name("") == ""


def test_caller_supplied_label_beats_the_cache_but_not_an_override():
    assert C.carrier_display_name("newcarrier", "New Carrier") == "New Carrier"
    # An override exists precisely because the API's own label is unhelpful, so
    # it must win over whatever the response said.
    assert C.carrier_display_name("royalmailv3", "RoyalMailV3") == "Royal Mail V3"


# ---------------------------------------------------------------------------
# Service levels
# ---------------------------------------------------------------------------


def test_service_levels_are_parsed_and_cached():
    manager = _manager(METADATA)
    with patch("app.services.carriers.client_manager", manager):
        levels = C.list_service_levels()

    manager.get_client.return_value.carrier_metadata.retrieve.assert_called_once_with(
        types=["service_levels"]
    )
    assert {s.name for s in levels} == {
        "RoyalMail2ndClassSignedFor", "RoyalMail1stClass", "First"
    }
    signed = next(s for s in levels if s.name == "RoyalMail2ndClassSignedFor")
    assert signed.carrier == "royalmailv3"
    assert signed.dimensions == "13.9in x 9.84in x 0.98in"
    assert signed.max_weight == 26.46

    # Now available offline.
    with patch("app.services.carriers.client_manager", _manager(side_effect=ConnectionError())):
        cached = C.list_service_levels()
    assert len(cached) == 3


def test_service_codes_are_never_re_cased():
    """A service name is passed to EasyPost verbatim; re-casing it anywhere
    would produce a value the API rejects."""
    with patch("app.services.carriers.client_manager", _manager(METADATA)):
        levels = C.list_service_levels()
    assert "RoyalMail2ndClassSignedFor" in {s.name for s in levels}
    assert all(s.carrier.islower() for s in levels)


def test_refresh_replaces_rather_than_accumulates():
    with patch("app.services.carriers.client_manager", _manager(METADATA)):
        C.list_service_levels()
        C.list_service_levels()
    with patch("app.services.carriers.client_manager", _manager(side_effect=ConnectionError())):
        assert len(C.list_service_levels()) == 3


def test_failure_with_empty_cache_returns_empty():
    with patch("app.services.carriers.client_manager", _manager(side_effect=ConnectionError())):
        assert C.list_service_levels() == []


def test_service_levels_for_carrier_matches_case_insensitively():
    with patch("app.services.carriers.client_manager", _manager(METADATA)):
        C.list_service_levels()
    assert len(C.service_levels_for_carrier("RoyalMailV3")) == 2
    assert len(C.service_levels_for_carrier("royalmailv3")) == 2
    assert C.service_levels_for_carrier("nosuchcarrier") == []


def test_entries_missing_a_name_are_dropped():
    with patch("app.services.carriers.client_manager", _manager([
        {"name": "x", "human_readable": "X",
         "service_levels": [{"carrier": "x", "name": ""}, {"carrier": "x", "name": "Good"}]},
    ])):
        levels = C.list_service_levels()
    assert [s.name for s in levels] == ["Good"]


# ---------------------------------------------------------------------------
# The signature precondition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["RoyalMail2ndClassSignedFor", "Tracked48Signature", "RoyalMail1stClassSignedFor"],
)
def test_signed_services_are_flagged_as_needing_the_signature_option(name):
    """Verified live: a batch naming a SignedFor service without
    options.delivery_confirmation = SIGNATURE reaches `created` and then fails
    at purchase with "does not offer service". Adding the option, and changing
    nothing else, purchases."""
    assert C.ServiceLevel(carrier="royalmailv3", name=name).requires_signature


@pytest.mark.parametrize("name", ["RoyalMail1stClass", "Tracked48", "First"])
def test_ordinary_services_are_not_flagged(name):
    assert not C.ServiceLevel(carrier="royalmailv3", name=name).requires_signature


# ---------------------------------------------------------------------------
# Enabled carrier accounts
# ---------------------------------------------------------------------------


def _account_manager(accounts):
    client = Mock()
    client.carrier_account.all.return_value = accounts
    manager = Mock()
    manager.get_client.return_value = client
    return manager


def test_carrier_accounts_unknown_in_test_mode_is_none_not_empty():
    """A test key gets a ForbiddenError from this endpoint. "Unknown" must not
    be confused with "no carriers enabled", or the picker would show nothing."""
    manager = Mock()
    manager.get_client.return_value.carrier_account.all.side_effect = RuntimeError("Forbidden")
    with patch("app.services.carriers.client_manager", manager):
        assert C.list_carrier_accounts() is None
        assert C.enabled_carrier_codes() is None


def test_account_type_maps_to_a_metadata_carrier_code():
    ref = C.CarrierAccountRef(id="ca_1", type="RoyalMailV3Account", readable="Royal Mail V3")
    assert ref.carrier_code == "royalmailv3"


def test_accounts_whose_type_does_not_match_are_recovered_by_their_label():
    """Real production accounts where the type alone fails: HermesAccount is the
    carrier "evri", DhlEcsAccount is "dhlecommercesolutions", and
    DhlExpressDefaultAccount is "dhlexpress". All three match on the account's
    readable label against the carrier's human_readable."""
    with patch("app.services.carriers.client_manager", _manager([
        {"name": "evri", "human_readable": "Evri", "service_levels": []},
        {"name": "dhlecommercesolutions", "human_readable": "DHL eCommerce", "service_levels": []},
        {"name": "dhlexpress", "human_readable": "DHL Express", "service_levels": []},
    ])):
        C.list_service_levels()

    with patch("app.services.carriers.client_manager", _account_manager([
        {"id": "ca_1", "type": "HermesAccount", "readable": "Evri"},
        {"id": "ca_2", "type": "DhlEcsAccount", "readable": "DHL eCommerce"},
        {"id": "ca_3", "type": "DhlExpressDefaultAccount", "readable": "DHL Express"},
    ])):
        codes = C.enabled_carrier_codes()

    assert {"evri", "dhlecommercesolutions", "dhlexpress"}.issubset(codes)
