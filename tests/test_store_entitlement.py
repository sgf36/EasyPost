"""Microsoft Store 'Production unlock' entitlement logic.

The real Windows.Services.Store calls can only run inside a packaged Store app,
so here we mock the single live-ownership probe (`_owns_unlock_live`) and settings
storage, and assert the decision tree around it: grace-first for the routine
check, forced-live for refresh, and safe degradation off the Store.
"""

from datetime import timedelta

import pytest

import app.core.store_entitlement as se
from app.core.settings import AppSettings
from app.core.store_entitlement import PurchaseResult


@pytest.fixture
def mem_settings(monkeypatch):
    """In-memory settings so the entitlement stamp never touches disk."""
    state = {"s": AppSettings()}
    monkeypatch.setattr(se, "load_settings", lambda: state["s"])
    monkeypatch.setattr(se, "save_settings", lambda s: state.__setitem__("s", s))
    return state


@pytest.fixture
def store_build(monkeypatch):
    monkeypatch.setattr(se, "STORE_BUILD", True)


# --- production_unlocked: the routine, grace-first gate ------------------

def test_not_a_store_build_is_always_locked(monkeypatch, mem_settings):
    monkeypatch.setattr(se, "STORE_BUILD", False)
    assert se.production_unlocked() is False
    assert se.refresh_entitlement() is False


def test_active_grace_unlocks_without_asking_the_store(monkeypatch, mem_settings, store_build):
    mem_settings["s"].store_unlock_confirmed_at = se._now().isoformat()
    called = {"live": False}

    def _live():
        called["live"] = True
        return None

    monkeypatch.setattr(se, "_owns_unlock_live", _live)
    assert se.production_unlocked() is True
    assert called["live"] is False  # grace short-circuits the network call


def test_expired_grace_falls_through_to_a_live_check(monkeypatch, mem_settings, store_build):
    stale = (se._now() - timedelta(days=se.STORE_UNLOCK_GRACE_DAYS + 1)).isoformat()
    mem_settings["s"].store_unlock_confirmed_at = stale
    monkeypatch.setattr(se, "_owns_unlock_live", lambda: True)
    assert se.production_unlocked() is True
    assert mem_settings["s"].store_unlock_confirmed_at != stale  # stamp refreshed


def test_live_owned_stamps_and_unlocks(monkeypatch, mem_settings, store_build):
    monkeypatch.setattr(se, "_owns_unlock_live", lambda: True)
    assert se.production_unlocked() is True
    assert mem_settings["s"].store_unlock_confirmed_at is not None


def test_live_not_owned_locks_and_clears_stamp(monkeypatch, mem_settings, store_build):
    mem_settings["s"].store_unlock_confirmed_at = None
    monkeypatch.setattr(se, "_owns_unlock_live", lambda: False)
    assert se.production_unlocked() is False


def test_unknown_and_no_grace_is_locked(monkeypatch, mem_settings, store_build):
    monkeypatch.setattr(se, "_owns_unlock_live", lambda: None)
    assert se.production_unlocked() is False


# --- refresh_entitlement: forced live, used after purchase / Restore -----

def test_refresh_overrides_grace_when_store_says_not_owned(monkeypatch, mem_settings, store_build):
    mem_settings["s"].store_unlock_confirmed_at = se._now().isoformat()
    monkeypatch.setattr(se, "_owns_unlock_live", lambda: False)
    assert se.refresh_entitlement() is False
    assert mem_settings["s"].store_unlock_confirmed_at is None


def test_refresh_falls_back_to_grace_when_store_unreachable(monkeypatch, mem_settings, store_build):
    mem_settings["s"].store_unlock_confirmed_at = se._now().isoformat()
    monkeypatch.setattr(se, "_owns_unlock_live", lambda: None)
    assert se.refresh_entitlement() is True


# --- degradation off the Store ------------------------------------------

def test_store_context_is_none_off_store(monkeypatch):
    monkeypatch.setattr(se, "STORE_BUILD", False)
    assert se._store_context() is None


def test_purchase_unavailable_off_store(monkeypatch):
    monkeypatch.setattr(se, "_store_context", lambda: None)
    assert se.purchase_unlock(123) is PurchaseResult.UNAVAILABLE


def test_purchase_unavailable_without_store_id(monkeypatch):
    monkeypatch.setattr(se, "_store_context", lambda: object())
    monkeypatch.setattr(se, "STORE_ADDON_STORE_ID", "")
    assert se.purchase_unlock(123) is PurchaseResult.UNAVAILABLE


def test_store_listing_uri_targets_the_app_pdp():
    assert se.store_listing_uri() == f"ms-windows-store://pdp/?ProductId={se.APP_STORE_ID}"
