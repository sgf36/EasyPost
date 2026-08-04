"""Mac App Store 'Production Unlock' entitlement logic.

The real StoreKit calls can only run inside a MAS-installed app, so here we mock
the single live-ownership probe (`_owns_unlock_live`) and settings storage, and
assert the decision tree around it: grace-first for the routine check,
forced-live for refresh, and safe degradation off the Store. Mirrors
tests/test_store_entitlement.py so the two channels stay behaviourally identical.
"""

from datetime import timedelta

import pytest

import app.core.mac_store_entitlement as mse
from app.core.settings import AppSettings
from app.core.mac_store_entitlement import PurchaseResult


@pytest.fixture
def mem_settings(monkeypatch):
    """In-memory settings so the entitlement stamp never touches disk."""
    state = {"s": AppSettings()}
    monkeypatch.setattr(mse, "load_settings", lambda: state["s"])
    monkeypatch.setattr(mse, "save_settings", lambda s: state.__setitem__("s", s))
    return state


@pytest.fixture
def mas_build(monkeypatch):
    monkeypatch.setattr(mse, "MAS_BUILD", True)


# --- production_unlocked: the routine, grace-first gate ------------------

def test_not_a_mas_build_is_always_locked(monkeypatch, mem_settings):
    monkeypatch.setattr(mse, "MAS_BUILD", False)
    assert mse.production_unlocked() is False
    assert mse.refresh_entitlement() is False


def test_active_grace_unlocks_without_asking_storekit(monkeypatch, mem_settings, mas_build):
    mem_settings["s"].store_unlock_confirmed_at = mse._now().isoformat()
    called = {"live": False}

    def _live():
        called["live"] = True
        return None

    monkeypatch.setattr(mse, "_owns_unlock_live", _live)
    assert mse.production_unlocked() is True
    assert called["live"] is False  # grace short-circuits the StoreKit call


def test_expired_grace_falls_through_to_a_live_check(monkeypatch, mem_settings, mas_build):
    stale = (mse._now() - timedelta(days=mse.STORE_UNLOCK_GRACE_DAYS + 1)).isoformat()
    mem_settings["s"].store_unlock_confirmed_at = stale
    monkeypatch.setattr(mse, "_owns_unlock_live", lambda: True)
    assert mse.production_unlocked() is True
    assert mem_settings["s"].store_unlock_confirmed_at != stale  # stamp refreshed


def test_live_owned_stamps_and_unlocks(monkeypatch, mem_settings, mas_build):
    monkeypatch.setattr(mse, "_owns_unlock_live", lambda: True)
    assert mse.production_unlocked() is True
    assert mem_settings["s"].store_unlock_confirmed_at is not None


def test_live_not_owned_locks_and_clears_stamp(monkeypatch, mem_settings, mas_build):
    mem_settings["s"].store_unlock_confirmed_at = None
    monkeypatch.setattr(mse, "_owns_unlock_live", lambda: False)
    assert mse.production_unlocked() is False


def test_unknown_and_no_grace_is_locked(monkeypatch, mem_settings, mas_build):
    monkeypatch.setattr(mse, "_owns_unlock_live", lambda: None)
    assert mse.production_unlocked() is False


# --- refresh_entitlement: forced live, used after purchase / Restore -----

def test_refresh_overrides_grace_when_storekit_says_not_owned(monkeypatch, mem_settings, mas_build):
    mem_settings["s"].store_unlock_confirmed_at = mse._now().isoformat()
    monkeypatch.setattr(mse, "_owns_unlock_live", lambda: False)
    assert mse.refresh_entitlement() is False
    assert mem_settings["s"].store_unlock_confirmed_at is None


def test_refresh_falls_back_to_grace_when_storekit_unreachable(monkeypatch, mem_settings, mas_build):
    mem_settings["s"].store_unlock_confirmed_at = mse._now().isoformat()
    monkeypatch.setattr(mse, "_owns_unlock_live", lambda: None)
    assert mse.refresh_entitlement() is True


# --- degradation off the Store ------------------------------------------

def test_storekit_is_none_off_store(monkeypatch):
    monkeypatch.setattr(mse, "MAS_BUILD", False)
    assert mse._storekit() is None


def test_owns_live_is_none_when_storekit_unavailable(monkeypatch):
    monkeypatch.setattr(mse, "_storekit", lambda: None)
    assert mse._owns_unlock_live() is None


def test_purchase_unavailable_off_store(monkeypatch):
    monkeypatch.setattr(mse, "_storekit", lambda: None)
    assert mse.purchase_unlock(123) is PurchaseResult.UNAVAILABLE


def test_purchase_unavailable_when_payments_disallowed(monkeypatch):
    monkeypatch.setattr(mse, "_storekit", lambda: object())
    monkeypatch.setattr(mse, "_can_make_payments", lambda: False)
    assert mse.purchase_unlock(123) is PurchaseResult.UNAVAILABLE


def test_store_listing_uri_uses_numeric_id_when_known(monkeypatch):
    monkeypatch.setattr(mse, "MAC_APP_STORE_ID", "1234567890")
    assert mse.store_listing_uri() == "macappstore://apps.apple.com/app/id1234567890"


def test_store_listing_uri_falls_back_to_search_without_id(monkeypatch):
    monkeypatch.setattr(mse, "MAC_APP_STORE_ID", "")
    assert mse.store_listing_uri().startswith("macappstore://apps.apple.com/search")
