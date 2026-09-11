"""Re-check the tracker endpoint's carrier names against EasyPost itself.

carriers._TRACKER_CARRIER_NAMES exists because the tracker endpoint refuses some
catalogue codes. That is a fact about somebody else's API, so it can change in
either direction without this repository changing at all — this test is how
anyone finds out.

Test mode only, and free: EasyPost's EZ test tracking codes never reach a
carrier. Requires your EasyPost *test* key and is skipped without it (and so in
CI):

    EASYPOST_TEST_API_KEY=test_xxx pytest tests/tracker_carriers_live_test.py -v
"""

import os

import pytest

from app.services import carriers as C

API_KEY = os.environ.get("EASYPOST_TEST_API_KEY")

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="Set EASYPOST_TEST_API_KEY to run the live tracker-carrier check."
)

CODE = "EZ1000000001"

# Catalogue carriers the tracker endpoint refused under every spelling tried on
# 2026-09-11, or accepted only under a spelling that names a different carrier
# (see the comment on _TRACKER_CARRIER_NAMES). Listed so that one of them
# becoming trackable, or a new carrier becoming untrackable, fails loudly.
KNOWN_UNTRACKABLE = {
    "cdllastmilesolutions",
    "cirroecommerce",
    "cttexpress",
    "epostglobal",
    "nextdayexpress",
    "sekoomniparcel",
}


@pytest.fixture(scope="module")
def client():
    import easypost

    return easypost.EasyPostClient(API_KEY)


def _accepts(client, carrier: str) -> bool:
    """True if the endpoint takes this carrier. Only the specific "not
    supported" refusal counts as a refusal: an auth or network failure raises,
    so it can never be reported as a carrier being unsupported."""
    try:
        tracker = client.tracker.create(tracking_code=CODE, carrier=carrier)
    except Exception as exc:  # noqa: BLE001 - narrowed by message below
        if "not supported" in str(exc):
            return False
        raise
    assert getattr(tracker, "mode", None) == "test", "refusing to continue outside test mode"
    return True


def test_the_endpoint_refuses_a_carrier_it_does_not_know(client):
    # Positive control. If a made-up carrier were accepted, acceptance would
    # prove nothing and every assertion below would pass for the wrong reason.
    assert not _accepts(client, "notarealcarrierxyz")


@pytest.mark.parametrize("source, target", sorted(C._TRACKER_CARRIER_NAMES.items()))
def test_each_mapping_is_still_needed_and_still_works(client, source, target):
    assert not _accepts(client, source), (
        f"{source!r} is now accepted as it is; remove it from _TRACKER_CARRIER_NAMES"
    )
    assert _accepts(client, target), f"{target!r} is no longer accepted for {source!r}"


def test_every_catalogue_carrier_is_trackable_or_accounted_for(client):
    catalogue = client.carrier_metadata.retrieve()
    codes = sorted({entry.get("name") for entry in catalogue if entry.get("name")})
    refused = {code for code in codes if not _accepts(client, C.tracker_carrier_name(code))}

    unexpected = sorted(refused - KNOWN_UNTRACKABLE)
    assert not unexpected, (
        f"the tracker endpoint refuses these catalogue carriers: {unexpected}. "
        "Find the spelling it accepts and map it, or list it in KNOWN_UNTRACKABLE."
    )
    resolved = sorted(KNOWN_UNTRACKABLE - refused)
    assert not resolved, (
        f"no longer refused, or gone from the catalogue: {resolved}. "
        "Remove them from KNOWN_UNTRACKABLE."
    )
