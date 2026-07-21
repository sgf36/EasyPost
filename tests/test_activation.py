"""What activation must never get wrong.

Seat counting introduced a server the app previously did not need, so these
tests are mostly about the promises made to justify that: the machine never
leaves the device, a receipt only works where it was issued, and an outage of
ours does not lock a paying customer out.
"""

import base64
import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import activation, license as license_mod


@pytest.fixture
def signing_key(monkeypatch):
    """Swap the embedded public key for a throwaway pair we can sign with."""
    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    monkeypatch.setattr(license_mod, "LICENSE_PUBLIC_KEY_B64", public_b64)
    monkeypatch.setattr(activation, "LICENSE_PUBLIC_KEY_B64", public_b64)
    return private


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign_receipt(private, **overrides):
    payload = {
        "v": 1,
        "order": "ORD-1",
        "device": "a" * 32,
        "tier": "business",
        "seats": 25,
        "iat": "2026-01-01T00:00:00Z",
        "exp": "2027-01-01T00:00:00Z",
    }
    payload.update(overrides)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{activation.RECEIPT_TAG}.{_b64url(raw)}.{_b64url(private.sign(raw))}"


# --- the machine identifier stays on the machine ---------------------------

def test_device_hash_never_contains_the_machine_id():
    machine = activation.machine_id()
    digest = activation.device_hash("EPD1.some.key")
    assert machine not in digest
    assert digest != machine


def test_device_hash_is_unlinkable_across_licences():
    """Two customers on one computer must not be correlatable."""
    assert activation.device_hash("EPD1.key.one") != activation.device_hash("EPD1.key.two")


def test_device_hash_is_stable_for_one_licence():
    assert activation.device_hash("EPD1.key.one") == activation.device_hash("EPD1.key.one")


def test_device_hash_is_hex_of_fixed_width():
    digest = activation.device_hash("EPD1.key.one")
    assert len(digest) == 32
    assert all(c in "0123456789abcdef" for c in digest)


# --- receipts --------------------------------------------------------------

def test_valid_receipt_verifies(signing_key):
    receipt = activation.verify_receipt(sign_receipt(signing_key), "a" * 32)
    assert receipt is not None
    assert receipt.tier == "business"
    assert receipt.seats == 25
    assert not receipt.provisional


def test_receipt_is_inert_on_another_computer(signing_key):
    """Copying the config to a second machine must not carry the seat with it."""
    assert activation.verify_receipt(sign_receipt(signing_key), "b" * 32) is None


def test_tampered_receipt_is_rejected(signing_key):
    token = sign_receipt(signing_key)
    tag, payload, sig = token.split(".")
    forged = json.dumps({
        "v": 1, "order": "ORD-1", "device": "a" * 32, "tier": "enterprise",
        "seats": 0, "iat": "2026-01-01T00:00:00Z", "exp": "2099-01-01T00:00:00Z",
    }, sort_keys=True, separators=(",", ":")).encode()
    assert activation.verify_receipt(f"{tag}.{_b64url(forged)}.{sig}", "a" * 32) is None


def test_receipt_signed_by_the_wrong_key_is_rejected(signing_key):
    other = Ed25519PrivateKey.generate()
    assert activation.verify_receipt(sign_receipt(other), "a" * 32) is None


def test_expired_receipt_reports_itself_expired(signing_key):
    receipt = activation.verify_receipt(
        sign_receipt(signing_key, exp="2020-01-01T00:00:00Z"), "a" * 32
    )
    assert receipt is not None and receipt.is_expired


def test_unparseable_expiry_fails_closed(signing_key):
    receipt = activation.verify_receipt(sign_receipt(signing_key, exp="whenever"), "a" * 32)
    assert receipt is not None and receipt.is_expired


@pytest.mark.parametrize("junk", ["", "not-a-receipt", "EPDR1.only.two", "XXXX.a.b", None])
def test_malformed_receipts_never_raise(junk):
    assert activation.verify_receipt(junk, "a" * 32) is None


# --- an outage of ours is not the customer's problem -----------------------

def test_unreachable_server_is_its_own_error_type(monkeypatch):
    """Transport failure must be distinguishable, because it means grace."""
    import requests

    def explode(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "post", explode)
    with pytest.raises(activation.ActivationUnreachable):
        activation._post("/activate", {})


def test_server_error_is_treated_as_unreachable(monkeypatch):
    """A 500 is our fault, so it must grant grace rather than refuse."""
    import requests

    class Response:
        status_code = 503
        ok = False

        def json(self):
            return {}

    monkeypatch.setattr(requests, "post", lambda *a, **k: Response())
    with pytest.raises(activation.ActivationUnreachable):
        activation._post("/activate", {})


def test_seats_exhausted_carries_the_device_list(monkeypatch):
    """The user has to be able to choose which computer to release."""
    import requests

    class Response:
        status_code = 409
        ok = False

        def json(self):
            return {"error": "full", "devices": [{"device": "a" * 32, "label": "Old laptop"}]}

    monkeypatch.setattr(requests, "post", lambda *a, **k: Response())
    with pytest.raises(activation.SeatsExhausted) as caught:
        activation._post("/activate", {})
    assert caught.value.devices[0]["label"] == "Old laptop"


def test_revoked_licence_is_its_own_error(monkeypatch):
    import requests

    class Response:
        status_code = 403
        ok = False

        def json(self):
            return {"error": "refunded"}

    monkeypatch.setattr(requests, "post", lambda *a, **k: Response())
    with pytest.raises(activation.LicenseRevoked):
        activation._post("/activate", {})


def test_grace_receipt_is_marked_provisional(signing_key):
    info = license_mod.LicenseInfo(
        email="a@b.com", order="ORD-1", product="easypost-desktop",
        issued_at="2026-01-01T00:00:00Z", tier="business", seats=25,
    )
    until = activation._now() + timedelta(days=activation.GRACE_DAYS)
    receipt = activation._grace_receipt(info, "a" * 32, until)
    assert receipt.provisional
    assert not receipt.is_expired
    assert receipt.seats == 25


# --- proof of possession ---------------------------------------------------

def test_proof_depends_on_every_field():
    """Reordering or swapping a field must change the signature."""
    base = activation._sign_request("KEY", "ORD-1", "a" * 32, "2026-01-01T00:00:00Z")
    assert base != activation._sign_request("KEY", "ORD-2", "a" * 32, "2026-01-01T00:00:00Z")
    assert base != activation._sign_request("KEY", "ORD-1", "b" * 32, "2026-01-01T00:00:00Z")
    assert base != activation._sign_request("KEY", "ORD-1", "a" * 32, "2026-01-02T00:00:00Z")
    assert base != activation._sign_request("OTHER", "ORD-1", "a" * 32, "2026-01-01T00:00:00Z")


def test_proof_is_not_the_licence_key():
    """The key itself must never be derivable from what goes over the wire."""
    proof = activation._sign_request("SECRET-KEY", "ORD-1", "a" * 32, "2026-01-01T00:00:00Z")
    assert "SECRET-KEY" not in proof
    assert len(proof) == 64


# --- tiers -----------------------------------------------------------------

@pytest.mark.parametrize("tier,seats", [
    ("personal", 3), ("business", 25), ("organisation", 50), ("enterprise", 0),
])
def test_tier_table_matches_what_is_sold(tier, seats):
    assert license_mod.TIERS[tier] == seats


def test_uncapped_tier_reads_as_unlimited():
    info = license_mod.LicenseInfo(
        email="a@b.com", order="O", product="easypost-desktop",
        issued_at="", tier="enterprise", seats=0,
    )
    assert info.is_uncapped
    assert info.seat_summary() == "unlimited computers"


def test_seat_summary_is_not_pluralised_wrongly():
    info = license_mod.LicenseInfo(
        email="a@b.com", order="O", product="easypost-desktop",
        issued_at="", tier="custom", seats=1,
    )
    assert info.seat_summary() == "up to 1 computer"
