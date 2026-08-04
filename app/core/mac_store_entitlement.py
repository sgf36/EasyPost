"""Mac App Store 'Production Unlock' entitlement — MAS build only.

The macOS twin of ``app/core/store_entitlement.py``. The MAS build is free and
runs fully in EasyPost **test** mode; production — real labels, real money — is
gated behind a one-time StoreKit In-App Purchase, the ``production_unlock``
non-consumable. Ownership is read from StoreKit on-device (the app receipt /
current entitlements); there is no pasted key and no server of ours in the loop.
Apple mandates StoreKit for a Mac App Store sale, so the Ed25519 licence path and
the Windows Store add-on path are both unavailable here — this module is the
third channel.

Public surface is identical to the Windows module so ``license.py``,
``main_window.py`` and ``store_unlock.py`` wire to it symmetrically:

    IN_APP_OFFER_TOKEN, STORE_UNLOCK_GRACE_DAYS, PurchaseResult,
    production_unlocked(), refresh_entitlement(), purchase_unlock(hwnd=None),
    store_listing_uri()

**StoreKit approach (A): StoreKit 1 via PyObjC** (``pyobjc-framework-StoreKit``).
For a single non-consumable, ``restoreCompletedTransactions`` answers "does this
Apple ID own the unlock?" without hand-rolling receipt (PKCS#7/ASN.1) parsing —
which Apple warns is easy to get wrong. StoreKit 2 is Swift-async and awkward
from PyObjC (brief §10), so StoreKit 1 is the pragmatic path for one product.

Everything degrades safely off the Mac App Store. On a dev run, a non-macOS OS,
or with the StoreKit binding missing, the read path reports *locked* (``None`` /
``False``) and the purchase path reports *unavailable* rather than raising — so
importing this module can never break a non-MAS build, exactly as the Windows
module guards ``winrt``.

Offline grace: once ownership is confirmed we stamp
``settings.store_unlock_confirmed_at`` (the same field the Windows module uses —
a build is only ever one channel, so there is no collision) and trust it for
``STORE_UNLOCK_GRACE_DAYS`` without a live check. A first launch offline, before
StoreKit has surfaced the transaction, therefore never wrongly locks a payer out
of their own production mode — we fail toward the paying customer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from app.config import MAS_BUILD
from app.core.settings import load_settings, save_settings

# The In-App Purchase product identifier, assigned in App Store Connect. Ownership
# is matched on this. MUST equal the Product ID given to the "Production Unlock"
# non-consumable (reuses the Windows token for cross-channel consistency).
IN_APP_OFFER_TOKEN = "production_unlock"

# The app's numeric Apple ID (App Store Connect → App Information → "Apple ID"),
# for the macappstore:// deep-link fallback. Filled once the app record exists;
# empty is tolerated (store_listing_uri then points at the generic search).
MAC_APP_STORE_ID = "6797912453"

# How long a confirmed unlock is trusted without a fresh StoreKit check.
STORE_UNLOCK_GRACE_DAYS = 30

# Bound (seconds) for a StoreKit restore/product round-trip before we treat the
# question as "could not answer" (None) and lean on grace. StoreKit 1 callbacks
# are delivered on the main run loop, which we spin briefly from the worker
# thread the gate calls us on.
_STOREKIT_TIMEOUT = 20.0


class PurchaseResult(Enum):
    """Outcome of an in-app purchase attempt."""

    PURCHASED = "purchased"          # bought now, or already owned
    NOT_PURCHASED = "not_purchased"  # user cancelled
    UNAVAILABLE = "unavailable"      # StoreKit not usable here — use the deep link
    ERROR = "error"                  # a network/StoreKit error; try again


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- grace helpers (copied in spirit from the Windows module) -------------

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


# --- StoreKit plumbing (all guarded; None/UNAVAILABLE off the Store) ------

def _storekit():
    """The StoreKit module, or None if it cannot be used here (non-MAS build,
    dev run, or the pyobjc StoreKit binding is absent)."""
    if not MAS_BUILD:
        return None
    try:
        import StoreKit  # type: ignore

        return StoreKit
    except Exception:
        return None


def _can_make_payments() -> Optional[bool]:
    """Whether this device is allowed to make payments, or None if StoreKit is
    unusable. A definitive False (e.g. parental restrictions) is a real answer;
    an unusable StoreKit is not."""
    sk = _storekit()
    if sk is None:
        return None
    try:
        return bool(sk.SKPaymentQueue.canMakePayments())
    except Exception:
        return None


def _owns_unlock_live() -> Optional[bool]:
    """Ask StoreKit, right now, whether this Apple ID owns the unlock.

    Returns True/False on a definitive answer, or None if the question could not
    be asked (StoreKit unavailable) or errored — the caller then leans on grace
    rather than treating an outage as 'not owned', so a payer is never locked out
    by a transient StoreKit hiccup. Mirrors ``_owns_unlock_live`` in the Windows
    module; it is the single seam the decision-tree tests mock.

    Implementation: restore completed transactions and look for the
    non-consumable ``IN_APP_OFFER_TOKEN``. Runs only inside a real MAS-installed
    app; everywhere else ``_storekit()`` is None and we return None immediately.
    """
    sk = _storekit()
    if sk is None:
        return None
    try:
        return _restore_owns_offer(sk, _STOREKIT_TIMEOUT)
    except Exception:
        return None


def _restore_owns_offer(sk, timeout: float) -> Optional[bool]:
    """Drive SKPaymentQueue.restoreCompletedTransactions and report whether the
    unlock is among the restored transactions. Returns None if StoreKit never
    reports back within ``timeout`` (treated as 'could not answer')."""
    import objc  # type: ignore
    from Foundation import NSObject  # type: ignore
    from CoreFoundation import (  # type: ignore
        CFRunLoopRunInMode,
        kCFRunLoopDefaultMode,
    )

    state = {"done": False, "owns": False, "error": False}

    class _RestoreObserver(NSObject):
        def paymentQueue_updatedTransactions_(self, queue, transactions):
            # Non-consumables surface as 'restored' (or 'purchased'); mark
            # ownership and finish each so the queue does not replay it.
            try:
                for txn in transactions:
                    st = txn.transactionState()
                    if st in (sk.SKPaymentTransactionStateRestored,
                              sk.SKPaymentTransactionStatePurchased):
                        pid = txn.payment().productIdentifier()
                        if str(pid) == IN_APP_OFFER_TOKEN:
                            state["owns"] = True
                    if st in (sk.SKPaymentTransactionStateRestored,
                              sk.SKPaymentTransactionStatePurchased,
                              sk.SKPaymentTransactionStateFailed):
                        queue.finishTransaction_(txn)
            except Exception:
                state["error"] = True

        def paymentQueueRestoreCompletedTransactionsFinished_(self, queue):
            state["done"] = True

        def paymentQueue_restoreCompletedTransactionsFailedWithError_(self, queue, error):
            state["error"] = True
            state["done"] = True

    observer = _RestoreObserver.alloc().init()
    queue = sk.SKPaymentQueue.defaultQueue()
    queue.addTransactionObserver_(observer)
    try:
        queue.restoreCompletedTransactions()
        waited = 0.0
        step = 0.1
        while not state["done"] and waited < timeout:
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, step, False)
            waited += step
    finally:
        queue.removeTransactionObserver_(observer)

    if state["error"] or not state["done"]:
        return None
    return bool(state["owns"])


# --- the public gate contract --------------------------------------------

def production_unlocked() -> bool:
    """Whether the MAS build may operate in production — the routine gate.

    Grace-first, so the common case is a fast offline check and StoreKit is not
    driven on every evaluation (this is called from startup routing and from
    client.get_client). A recently-confirmed unlock is trusted for
    STORE_UNLOCK_GRACE_DAYS. Only with no live grace do we ask StoreKit: a
    definitive *owned* refreshes the stamp and unlocks; anything else (not owned,
    or StoreKit could not answer and we have never confirmed) locks.

    A refund is honoured lazily — production keeps working until grace lapses (at
    most STORE_UNLOCK_GRACE_DAYS), deliberately erring toward the paying
    customer, exactly as the Windows and direct builds do.
    """
    if not MAS_BUILD:
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
    purchase and by "Restore Purchases". Returns the resulting unlocked state,
    falling back to grace only if StoreKit could not be reached."""
    if not MAS_BUILD:
        return False
    live = _owns_unlock_live()
    if live is True:
        _stamp_confirmed()
        return True
    if live is False:
        _clear_confirmed()
        return False
    # Could not reach StoreKit: don't punish a possibly-valid owner.
    return _grace_active()


def purchase_unlock(hwnd: Optional[int] = None) -> PurchaseResult:
    """Attempt the StoreKit in-app purchase of the Production Unlock product.

    ``hwnd`` is accepted for signature symmetry with the Windows module (the
    Store's purchase dialog needs an owner window there); StoreKit presents its
    own sheet, so it is ignored here.

    Returns UNAVAILABLE if StoreKit cannot be driven here (dev run, framework
    missing, payments disallowed) — the UI then falls back to
    :func:`store_listing_uri`. On a successful (or already-owned) purchase the
    unlock is stamped and PURCHASED is returned.
    """
    sk = _storekit()
    if sk is None:
        return PurchaseResult.UNAVAILABLE
    if _can_make_payments() is False:
        return PurchaseResult.UNAVAILABLE
    try:
        return _buy_offer(sk, _STOREKIT_TIMEOUT)
    except Exception:
        return PurchaseResult.ERROR


def _buy_offer(sk, timeout: float) -> PurchaseResult:
    """Fetch the product then add a payment, observing the queue until the
    transaction settles. Verified end-to-end on-device / with a StoreKit
    configuration file during StoreKit testing (brief §7)."""
    from Foundation import NSObject  # type: ignore
    from CoreFoundation import (  # type: ignore
        CFRunLoopRunInMode,
        kCFRunLoopDefaultMode,
    )

    # 1) Resolve the SKProduct for our identifier.
    prod_state = {"done": False, "product": None}

    class _ProductsDelegate(NSObject):
        def productsRequest_didReceiveResponse_(self, request, response):
            try:
                products = list(response.products())
                if products:
                    prod_state["product"] = products[0]
            except Exception:
                pass

        def requestDidFinish_(self, request):
            prod_state["done"] = True

        def request_didFailWithError_(self, request, error):
            prod_state["done"] = True

    ids = sk.NSSet.setWithObject_(IN_APP_OFFER_TOKEN) if hasattr(sk, "NSSet") else None
    if ids is None:
        from Foundation import NSSet  # type: ignore
        ids = NSSet.setWithObject_(IN_APP_OFFER_TOKEN)
    req = sk.SKProductsRequest.alloc().initWithProductIdentifiers_(ids)
    pdelegate = _ProductsDelegate.alloc().init()
    req.setDelegate_(pdelegate)
    req.start()
    waited = 0.0
    while not prod_state["done"] and waited < timeout:
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)
        waited += 0.1
    product = prod_state["product"]
    if product is None:
        return PurchaseResult.UNAVAILABLE

    # 2) Add the payment and observe until it settles.
    pay_state = {"done": False, "result": PurchaseResult.ERROR}

    class _PurchaseObserver(NSObject):
        def paymentQueue_updatedTransactions_(self, queue, transactions):
            try:
                for txn in transactions:
                    st = txn.transactionState()
                    if str(txn.payment().productIdentifier()) != IN_APP_OFFER_TOKEN:
                        continue
                    if st in (sk.SKPaymentTransactionStatePurchased,
                              sk.SKPaymentTransactionStateRestored):
                        pay_state["result"] = PurchaseResult.PURCHASED
                        queue.finishTransaction_(txn)
                        pay_state["done"] = True
                    elif st == sk.SKPaymentTransactionStateFailed:
                        # SKErrorPaymentCancelled == 2
                        cancelled = False
                        try:
                            cancelled = int(txn.error().code()) == 2
                        except Exception:
                            pass
                        pay_state["result"] = (
                            PurchaseResult.NOT_PURCHASED if cancelled
                            else PurchaseResult.ERROR
                        )
                        queue.finishTransaction_(txn)
                        pay_state["done"] = True
            except Exception:
                pay_state["result"] = PurchaseResult.ERROR
                pay_state["done"] = True

    observer = _PurchaseObserver.alloc().init()
    queue = sk.SKPaymentQueue.defaultQueue()
    queue.addTransactionObserver_(observer)
    try:
        payment = sk.SKPayment.paymentWithProduct_(product)
        queue.addPayment_(payment)
        waited = 0.0
        while not pay_state["done"] and waited < timeout:
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)
            waited += 0.1
    finally:
        queue.removeTransactionObserver_(observer)

    if pay_state["result"] is PurchaseResult.PURCHASED:
        _stamp_confirmed()
    if not pay_state["done"]:
        return PurchaseResult.ERROR
    return pay_state["result"]


def store_listing_uri() -> str:
    """A ``macappstore:`` deep link to the app's App Store page, where the unlock
    can be bought. The fallback when the in-app purchase cannot be driven."""
    if MAC_APP_STORE_ID:
        return f"macappstore://apps.apple.com/app/id{MAC_APP_STORE_ID}"
    # No numeric id yet: open the Mac App Store search for the app by name.
    return "macappstore://apps.apple.com/search?term=Easy-Post%20Desktop"
