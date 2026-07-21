"""Tests for the offline license system (app/core/license.py).

Each test swaps in a throwaway keypair so we exercise the real verification
logic without depending on the production signing key.
"""

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app.core.license as lic
from app.core.settings import AppSettings


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _mint(priv: Ed25519PrivateKey, payload: dict) -> str:
    pb = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"EPD1.{_b64url(pb)}.{_b64url(priv.sign(pb))}"


def _payload(**over) -> dict:
    p = {"v": 1, "product": "easypost-desktop", "email": "a@b.com",
         "order": "O1", "iat": "2026-01-01T00:00:00Z"}
    p.update(over)
    return p


@pytest.fixture
def signer(monkeypatch):
    """Generate a keypair and point the app's embedded public key at it."""
    priv = Ed25519PrivateKey.generate()
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(lic, "LICENSE_PUBLIC_KEY_B64", base64.b64encode(raw).decode())
    return priv


def test_valid_license_verifies(signer):
    info = lic.verify_license(_mint(signer, _payload()))
    assert info is not None
    assert info.email == "a@b.com"
    assert info.order == "O1"
    assert info.product == "easypost-desktop"


def test_tampered_payload_rejected(signer):
    tag, payload, sig = _mint(signer, _payload()).split(".")
    tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B")
    assert lic.verify_license(f"{tag}.{tampered}.{sig}") is None


def test_forged_signature_rejected(signer):
    other = Ed25519PrivateKey.generate()
    assert lic.verify_license(_mint(other, _payload())) is None


def test_wrong_product_rejected(signer):
    assert lic.verify_license(_mint(signer, _payload(product="something-else"))) is None


def test_wrong_version_rejected(signer):
    assert lic.verify_license(_mint(signer, _payload(v=2))) is None


@pytest.mark.parametrize("junk", ["", "not-a-key", "EPD1.only-two", "WRONG.aa.bb", "EPD1..", "....."])
def test_garbage_rejected(junk):
    assert lic.verify_license(junk) is None


def test_activate_persists_and_is_licensed(signer, monkeypatch):
    store = {"s": AppSettings()}
    monkeypatch.setattr(lic, "load_settings", lambda: store["s"])
    monkeypatch.setattr(lic, "save_settings", lambda s: store.__setitem__("s", s))

    assert lic.is_licensed() is False
    key = _mint(signer, _payload())
    assert lic.activate(key) is not None
    assert store["s"].license_key == key
    assert lic.is_licensed() is True

    lic.deactivate()
    assert store["s"].license_key is None
    assert lic.is_licensed() is False


def test_activate_rejects_bad_key(signer, monkeypatch):
    store = {"s": AppSettings()}
    monkeypatch.setattr(lic, "load_settings", lambda: store["s"])
    monkeypatch.setattr(lic, "save_settings", lambda s: store.__setitem__("s", s))
    assert lic.activate("EPD1.garbage.sig") is None
    assert store["s"].license_key is None
