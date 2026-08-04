"""The production-mode licence gate.

Test mode is free; production (real labels, real money) requires a licence in
direct-download builds. The gate must key off a key's TRUE mode, not the UI
field it was typed into, so a production key cannot be run for free by pasting
it into the test slot.
"""

import types

import pytest

import app.core.client as client_mod
import app.core.easypost_keys as ek
import app.core.license as lic
from app.config import MODE_PRODUCTION, MODE_TEST
from app.core.client import ClientManager, ProductionLicenseRequired
from app.core.credential_store import Credentials


# --- production_allowed --------------------------------------------------

def test_production_allowed_when_not_a_licensed_build(monkeypatch):
    """Unflagged dev build carries no gate flag: production is unrestricted.

    Patch every channel flag off explicitly so a stray build-variant flag left
    in the working tree (e.g. a packaging run's mas_build.flag) can't silently
    flip this case onto a gated branch.
    """
    monkeypatch.setattr(lic, "LICENSE_REQUIRED", False)
    monkeypatch.setattr(lic, "STORE_BUILD", False)
    monkeypatch.setattr(lic, "MAS_BUILD", False)
    monkeypatch.setattr(lic, "is_licensed", lambda: False)
    assert lic.production_allowed() is True


def test_production_blocked_without_licence_in_direct_build(monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_REQUIRED", True)
    monkeypatch.setattr(lic, "is_licensed", lambda: False)
    assert lic.production_allowed() is False


def test_production_allowed_with_licence_in_direct_build(monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_REQUIRED", True)
    monkeypatch.setattr(lic, "is_licensed", lambda: True)
    assert lic.production_allowed() is True


def test_store_build_defers_to_store_entitlement(monkeypatch):
    """Store build: production follows Store add-on ownership, not a key."""
    import app.core.store_entitlement as se

    monkeypatch.setattr(lic, "LICENSE_REQUIRED", False)
    monkeypatch.setattr(lic, "STORE_BUILD", True)

    monkeypatch.setattr(se, "production_unlocked", lambda: True)
    assert lic.production_allowed() is True

    monkeypatch.setattr(se, "production_unlocked", lambda: False)
    assert lic.production_allowed() is False


def test_mas_build_defers_to_mac_store_entitlement(monkeypatch):
    """Mac App Store build: production follows StoreKit ownership, not a key."""
    import app.core.mac_store_entitlement as mse

    monkeypatch.setattr(lic, "LICENSE_REQUIRED", False)
    monkeypatch.setattr(lic, "STORE_BUILD", False)
    monkeypatch.setattr(lic, "MAS_BUILD", True)

    monkeypatch.setattr(mse, "production_unlocked", lambda: True)
    assert lic.production_allowed() is True

    monkeypatch.setattr(mse, "production_unlocked", lambda: False)
    assert lic.production_allowed() is False


# --- detect_mode ---------------------------------------------------------

def _fake_easypost(mode_or_exc):
    """Build a stand-in easypost module whose address.create returns an object
    carrying `mode`, or raises if given an exception."""
    def create(**_kwargs):
        if isinstance(mode_or_exc, Exception):
            raise mode_or_exc
        return types.SimpleNamespace(mode=mode_or_exc)

    def EasyPostClient(_key):
        return types.SimpleNamespace(address=types.SimpleNamespace(create=create))

    return types.SimpleNamespace(EasyPostClient=EasyPostClient)


@pytest.mark.parametrize("reported", [MODE_TEST, MODE_PRODUCTION])
def test_detect_mode_reads_the_true_mode(monkeypatch, reported):
    monkeypatch.setattr(ek, "easypost", _fake_easypost(reported))
    assert ek.detect_mode("any-key") == reported


def test_detect_mode_none_on_api_failure(monkeypatch):
    monkeypatch.setattr(ek, "easypost", _fake_easypost(RuntimeError("bad key")))
    assert ek.detect_mode("any-key") is None


def test_detect_mode_none_on_empty_or_unexpected(monkeypatch):
    monkeypatch.setattr(ek, "easypost", _fake_easypost("something-else"))
    assert ek.detect_mode("") is None          # empty short-circuits
    assert ek.detect_mode("k") is None          # unrecognised mode value


# --- client-layer enforcement -------------------------------------------

def _manager(active_mode):
    mgr = ClientManager.__new__(ClientManager)  # skip __init__/keyring
    mgr._credentials = Credentials(
        test_key="EZTK_x", production_key="EZAK_x", active_mode=active_mode
    )
    return mgr


def test_get_client_blocks_production_without_licence(monkeypatch):
    monkeypatch.setattr(client_mod, "production_allowed", lambda: False)
    mgr = _manager(MODE_PRODUCTION)
    with pytest.raises(ProductionLicenseRequired):
        mgr.get_client()


def test_get_client_allows_production_with_licence(monkeypatch):
    monkeypatch.setattr(client_mod, "production_allowed", lambda: True)
    monkeypatch.setattr(client_mod.easypost, "EasyPostClient", lambda key: ("client", key))
    mgr = _manager(MODE_PRODUCTION)
    assert mgr.get_client() == ("client", "EZAK_x")


def test_get_client_never_blocks_test_mode(monkeypatch):
    """Test mode is free even in an unlicensed build."""
    monkeypatch.setattr(client_mod, "production_allowed", lambda: False)
    monkeypatch.setattr(client_mod.easypost, "EasyPostClient", lambda key: ("client", key))
    mgr = _manager(MODE_TEST)
    assert mgr.get_client() == ("client", "EZTK_x")
