"""The tracker endpoint's names for carriers, offline.

The Tracking page offers carriers from the metadata catalogue, and the tracker
endpoint refuses a few of those codes — Evri most visibly, which it knows only
as Hermes. Measured in test mode on 2026-09-11; the live re-check is
tests/tracker_carriers_live_test.py.
"""

from unittest.mock import Mock, patch

import pytest

from app.services import carriers as C
from app.services import tracking as T
from app.services.formatting import display_carrier


@pytest.fixture(autouse=True)
def _catalogue(monkeypatch):
    monkeypatch.setattr(
        C,
        "_carrier_names",
        {
            "evri": "Evri",
            "dhlecommercesolutions": "DHL eCommerce",
            "fedexgroundeconomy": "FedEx Ground Economy",
            "usps": "USPS",
        },
    )
    monkeypatch.setattr(C, "_account_labels", {})


@pytest.mark.parametrize(
    "sent, expected",
    [
        ("evri", "hermes"),
        ("Evri", "hermes"),  # typed, as the free-text box always allowed
        ("EVRI", "hermes"),
        ("dhlecommercesolutions", "dhlecs"),
        ("fedexgroundeconomy", "fedexsmartpost"),
    ],
)
def test_a_refused_catalogue_code_becomes_the_accepted_name(sent, expected):
    assert C.tracker_carrier_name(sent) == expected


@pytest.mark.parametrize(
    "sent", ["royalmailv3", "RoyalMailV3", "usps", "DHL eCommerce", "SomeCarrierNotInCatalogue", ""]
)
def test_everything_else_passes_through_untouched(sent):
    assert C.tracker_carrier_name(sent) == sent


@pytest.mark.parametrize("code", ["epostglobal", "sekoomniparcel"])
def test_ambiguous_carriers_are_not_guessed(code):
    # An accepted spelling exists for both, and both would be wrong: ePostGlobal
    # is accepted only as its V2 integration, SEKO OmniParcel as two different
    # carriers. A refusal is better than a tracker on the wrong carrier.
    assert C.tracker_carrier_name(code) == code


def test_create_tracker_sends_the_name_the_endpoint_accepts():
    client = Mock()
    with patch.object(T.client_manager, "get_client", return_value=client):
        T.create_tracker("EZ1000000001", "evri")
    client.tracker.create.assert_called_once_with(tracking_code="EZ1000000001", carrier="hermes")


def test_create_tracker_leaves_a_blank_carrier_to_auto_detection():
    client = Mock()
    with patch.object(T.client_manager, "get_client", return_value=client):
        T.create_tracker("EZ1000000001", "")
    client.tracker.create.assert_called_once_with(tracking_code="EZ1000000001", carrier=None)


def test_a_tracker_read_back_under_its_tracker_name_shows_the_chosen_carrier():
    # The user picked Evri; EasyPost stores the tracker as "Hermes".
    assert display_carrier("Hermes") == "Evri"
    assert display_carrier("DhlEcs") == "DHL eCommerce"


def test_a_target_that_is_a_carrier_in_its_own_right_keeps_its_own_name():
    assert display_carrier("FedExSmartPost") == "FedEx SmartPost"


def test_offline_with_no_catalogue_never_shows_the_source_code(monkeypatch):
    # With nothing cached the reverse lookup has no label to give, and must not
    # hand back the internal catalogue code in its place.
    monkeypatch.setattr(C, "_carrier_names", {})
    assert display_carrier("DhlEcs") != "dhlecommercesolutions"
    assert "dhlecommercesolutions" not in display_carrier("DhlEcs").casefold()
