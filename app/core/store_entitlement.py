"""Microsoft Store 'Production unlock' entitlement — Store build only.

The Store build is free and runs fully in EasyPost **test** mode. Production —
real labels, real money — is gated behind a one-time durable Store add-on,
"Production unlock". Ownership is read from ``Windows.Services.Store``; there is
no pasted key and no server of ours in the loop. This is the Store-native
equivalent of the Ed25519 licence path used by the direct-download build.

Everything here degrades safely off the Store. On a non-Windows OS, an
unpackaged/dev run, or with the ``winrt`` binding missing, the read path reports
*locked* and the purchase path reports *unavailable* rather than raising — so
importing this module can never break a non-Store build.

Offline grace: once ownership is confirmed we stamp
``settings.store_unlock_confirmed_at`` and trust it for ``STORE_UNLOCK_GRACE_DAYS``
without a live check, so a Store or network outage never locks a paying customer
out of their own production mode (mirrors the activation-receipt grace in
``app/core/activation.py``). Windows also caches the licence itself, so the live
check usually succeeds offline too — the grace is a belt-and-braces second line.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from app.config import STORE_BUILD
from app.core.settings import load_settings, save_settings

# The add-on's developer-assigned identifier ("Product ID" in Partner Center).
# Ownership is matched on this rather than the opaque Store ID, so the check
# keeps working regardless of market/SKU Store-ID variance. MUST match the
# Product ID given to the "Production unlock" add-on when it is created.
IN_APP_OFFER_TOKEN = "production_unlock"

# The add-on's Store ID (the 12-char product Store ID). Needed only for the
# in-app purchase call and the Store deep link — NOT for the ownership read.
# Set this once the add-on exists in Partner Center. Empty until then: the
# purchase path falls back to opening the app's own Store page.
STORE_ADDON_STORE_ID = ""

# The parent app's Store ID, for the Store deep-link fallback.
APP_STORE_ID = "9NDSDL5LV5B5"

# How long a confirmed unlock is trusted without a fresh Store check.
STORE_UNLOCK_GRACE_DAYS = 30


class PurchaseResult(Enum):
    """Outcome of an in-app purchase attempt."""

    PURCHASED = "purchased"          # bought now, or already owned
    NOT_PURCHASED = "not_purchased"  # user cancelled
    UNAVAILABLE = "unavailable"      # Store API not usable here — use the deep link
    ERROR = "error"                  # a network/server error; try again


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _await(operation):
    """Run a WinRT IAsyncOperation to completion on a private event loop.

    Called from a worker thread (never the UI thread), so a fresh loop is safe.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_as_coro(operation))
    finally:
        loop.close()


async def _as_coro(operation):
    return await operation


def _store_context():
    """A ``StoreContext`` for this app, or None if the Store API is unusable
    (non-Windows, unpackaged/dev run, or the winrt binding is absent)."""
    if not STORE_BUILD:
        return None
    try:
        from winrt.windows.services.store import StoreContext
    except Exception:
        return None
    try:
        return StoreContext.get_default()
    except Exception:
        return None


def _owns_unlock_live() -> Optional[bool]:
    """Ask the Store, right now, whether this account owns the unlock.

    Returns True/False on a definitive answer, or None if the question could
    not be asked (API unavailable) or errored — the caller then leans on grace
    rather than treating an outage as 'not owned'.
    """
    ctx = _store_context()
    if ctx is None:
        return None
    try:
        app_license = _await(ctx.get_app_license_async())
    except Exception:
        return None
    try:
        add_ons = app_license.add_on_licenses
    except Exception:
        return None
    # add_on_licenses is an IMapView[str, StoreLicense]. Access patterns vary
    # across binding versions, so try values() first, then item iteration, and
    # treat any structural surprise as 'could not determine' (None), never as a
    # hard 'not owned' that would lock out a payer on a binding quirk.
    licences = []
    try:
        licences = list(add_ons.values())
    except Exception:
        try:
            licences = [add_ons.lookup(k) for k in add_ons]
        except Exception:
            return None
    try:
        for lic in licences:
            if getattr(lic, "is_active", False) and (
                getattr(lic, "in_app_offer_token", "") == IN_APP_OFFER_TOKEN
            ):
                return True
        return False
    except Exception:
        return None


def _grace_active() -> bool:
    stamp = load_settings().store_unlock_confirmed_at
    if not stamp:
        return False
    try:
        confirmed = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if confirmed.tzinfo is None:
        confirmed = confirmed.replace(tzinfo=timezone.utc)
    return _now() - confirmed < timedelta(days=STORE_UNLOCK_GRACE_DAYS)


def _stamp_confirmed() -> None:
    settings = load_settings()
    settings.store_unlock_confirmed_at = _now().isoformat()
    save_settings(settings)


def _clear_confirmed() -> None:
    settings = load_settings()
    if settings.store_unlock_confirmed_at is not None:
        settings.store_unlock_confirmed_at = None
        save_settings(settings)


def production_unlocked() -> bool:
    """Whether the Store build may operate in production — the routine gate.

    Grace-first, so the common case is a fast offline check and the Store is not
    hit on every evaluation (this is called from startup routing and from
    client.get_client). A recently-confirmed unlock is trusted for
    STORE_UNLOCK_GRACE_DAYS. Only when there is no live grace do we ask the
    Store: a definitive *owned* refreshes the stamp and unlocks; anything else
    (not owned, or the API could not answer and we have never confirmed) locks.

    A refund is therefore honoured lazily — production keeps working until grace
    lapses (at most STORE_UNLOCK_GRACE_DAYS), deliberately erring toward the
    paying customer, exactly as the direct build trusts its activation receipt.
    """
    if not STORE_BUILD:
        # Not a Store build: this provider does not apply. license.py only
        # consults it when STORE_BUILD is set, but guard anyway.
        return False
    if _grace_active():
        return True
    live = _owns_unlock_live()
    if live is True:
        _stamp_confirmed()
        return True
    if live is False:
        _clear_confirmed()
    return False


def refresh_entitlement() -> bool:
    """Force a live ownership check, bypassing grace — used right after a
    purchase and by "Restore purchase". Returns the resulting unlocked state,
    falling back to grace only if the Store could not be reached."""
    if not STORE_BUILD:
        return False
    live = _owns_unlock_live()
    if live is True:
        _stamp_confirmed()
        return True
    if live is False:
        _clear_confirmed()
        return False
    # Could not reach the Store: don't punish a possibly-valid owner.
    return _grace_active()


def purchase_unlock(hwnd: Optional[int] = None) -> PurchaseResult:
    """Attempt the in-app purchase of the Production unlock add-on.

    ``hwnd`` is the parent window handle for the Store's modal purchase dialog.
    Returns UNAVAILABLE if the in-app purchase cannot be driven here (the UI
    then falls back to :func:`store_listing_uri`); the caller should open that
    so the customer can still buy from the Store app.
    """
    ctx = _store_context()
    if ctx is None or not STORE_ADDON_STORE_ID:
        return PurchaseResult.UNAVAILABLE
    if hwnd:
        # The purchase dialog is modal and needs an owner window. The
        # IInitializeWithWindow association differs across binding versions, so
        # attempt it best-effort; if it fails, fall back to the Store deep link
        # rather than showing an ownerless (and possibly invisible) dialog.
        try:
            _associate_window(ctx, hwnd)
        except Exception:
            return PurchaseResult.UNAVAILABLE
    try:
        result = _await(ctx.request_purchase_async(STORE_ADDON_STORE_ID))
    except Exception:
        return PurchaseResult.ERROR
    try:
        from winrt.windows.services.store import StorePurchaseStatus

        status = result.status
        if status in (StorePurchaseStatus.SUCCEEDED, StorePurchaseStatus.ALREADY_PURCHASED):
            _stamp_confirmed()
            return PurchaseResult.PURCHASED
        if status == StorePurchaseStatus.NOT_PURCHASED:
            return PurchaseResult.NOT_PURCHASED
        return PurchaseResult.ERROR
    except Exception:
        return PurchaseResult.ERROR


def _associate_window(ctx, hwnd: int) -> None:
    """Associate a StoreContext with an owner window via IInitializeWithWindow."""
    from winrt.windows.services.store import StoreContext  # noqa: F401
    # pywinrt exposes the interop as a module-level helper on some versions and
    # as a method on others; try both, letting the caller catch failure.
    try:
        import winrt.windows.services.store as store_ns

        store_ns.StoreContext._initialize_with_window(ctx, hwnd)  # type: ignore[attr-defined]
        return
    except Exception:
        pass
    ctx.initialize_with_window(hwnd)  # type: ignore[attr-defined]


def store_listing_uri() -> str:
    """A ``ms-windows-store:`` deep link to the app's Store page, where the
    add-on can be bought. The fallback when the in-app purchase is unavailable."""
    return f"ms-windows-store://pdp/?ProductId={APP_STORE_ID}"
