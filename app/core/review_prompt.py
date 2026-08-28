"""In-app store review prompt — Store and Mac App Store builds only.

The third sibling of ``app/core/store_entitlement.py`` (Microsoft Store) and
``app/core/mac_store_entitlement.py`` (Mac App Store), and it follows their
shape deliberately: one public surface, per-channel implementations behind it,
and every import guarded so that importing this module can never break a build
it does not apply to.

Why it exists: all three storefronts sat at **zero ratings**, and a zero-rated
listing both ranks and converts worse. Store buyers cannot be contacted by us —
neither Apple nor Microsoft hands over the customer — so asking inside the app
is the only route that reaches them at all.

**The direct-download build never prompts.** There is nowhere for those users to
leave a review: Microsoft requires ownership through the Store, and Mac App Store
reviews require the app to have come from it. Sending them to a listing they
cannot review is worse than not asking, so ``review_available()`` is False there
and the Help menu offers a passive GitHub link instead.

## What the platforms give back

Almost nothing, and the difference matters when reading the numbers:

- **Apple** — ``SKStoreReviewController.requestReview()`` may display nothing at
  all. The system decides, throttles to three prompts per 365 days per device,
  and never reports what happened. A call means *requested*, never *shown* and
  never *rated*.
- **Microsoft** — ``RequestRateAndReviewAppAsync`` does return a status, making
  it the only channel with any feedback whatsoever.

So the local counter here can only ever mean "we asked". Actual ratings are read
from the stores themselves — see ``tools/ratings_watch.py``.

## Policy

Two rules are load-bearing and must not be "improved":

- **No sentiment gating.** Asking "enjoying the app?" first and routing only the
  happy answers to the store violates Apple's guidelines and is the most common
  reason this feature gets an app rejected. Everyone who passes the gates gets
  the same system prompt.
- **No custom dialog.** Each platform's own call, or nothing. A homemade window
  that imitates the system one is both a rejection risk and less effective.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import MAS_BUILD, STORE_BUILD
from app.core.settings import load_settings, save_settings

# Deliberately conservative. Apple's own ceiling is three prompts per 365 days;
# staying below it means the system throttle is never the thing saying no.
SUCCESSES_BEFORE_PROMPT = 3
DAYS_SINCE_FIRST_RUN = 7
DAYS_BETWEEN_PROMPTS = 120
MAX_PROMPTS_EVER = 3

# Process-local, deliberately not persisted: it describes this run, not this
# install. Set when anything went wrong that would sour a prompt — a failed
# purchase, a refund, an API error, the licence gate. A prompt that follows
# friction collects the friction.
_session_friction = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp: Optional[str]) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def mark_session_friction() -> None:
    """Record that something went wrong this run, suppressing any prompt.

    Cheap and idempotent — call it freely from error paths.
    """
    global _session_friction
    _session_friction = True


def reset_session_friction() -> None:
    """Test seam. Not called in normal operation; a real session's friction
    should persist for the whole session."""
    global _session_friction
    _session_friction = False


def review_available() -> bool:
    """Whether this build has a storefront the user could actually review on."""
    return bool(STORE_BUILD or MAS_BUILD)


def ensure_first_run_stamp() -> datetime:
    """The install's first-run time, stamping it now if absent.

    Stamping on first read rather than at install means an existing install
    upgrading into this feature starts its seven-day clock at the upgrade, not
    at zero — so nobody is prompted the moment they update. That is intentional:
    a null stamp must never read as "infinitely old".
    """
    settings = load_settings()
    existing = _parse(settings.first_run_at)
    if existing is not None:
        return existing
    now = _now()
    settings.first_run_at = now.isoformat()
    save_settings(settings)
    return now


def note_successful_shipment() -> None:
    """Count one successful label or batch. Cheap; call on every success."""
    if not review_available():
        return
    settings = load_settings()
    settings.review_success_count = (settings.review_success_count or 0) + 1
    save_settings(settings)


def should_request_review() -> bool:
    """Whether every gate passes. Pure predicate — shows nothing, writes nothing
    except the first-run stamp, and is the seam the tests drive."""
    if not review_available():
        return False
    if _session_friction:
        return False

    # Production only: a test-mode user has not yet had the experience being
    # rated, and has paid nothing for it.
    try:
        from app.core.client import client_manager
        from app.core.license import production_allowed

        if not client_manager.is_production():
            return False
        if not production_allowed():
            return False
    except Exception:
        # If the mode cannot be determined, do not prompt. Failing closed here
        # costs one prompt; failing open asks a test-mode user to rate an app
        # they have not bought.
        return False

    settings = load_settings()

    if (settings.review_success_count or 0) < SUCCESSES_BEFORE_PROMPT:
        return False
    if (settings.review_prompt_count or 0) >= MAX_PROMPTS_EVER:
        return False

    if _now() - ensure_first_run_stamp() < timedelta(days=DAYS_SINCE_FIRST_RUN):
        return False

    last = _parse(settings.review_last_prompted_at)
    if last is not None and _now() - last < timedelta(days=DAYS_BETWEEN_PROMPTS):
        return False

    return True


def _record_prompted() -> None:
    settings = load_settings()
    settings.review_last_prompted_at = _now().isoformat()
    settings.review_prompt_count = (settings.review_prompt_count or 0) + 1
    save_settings(settings)


def maybe_request_review(hwnd: Optional[int] = None) -> bool:
    """Apply every gate and, if they all pass, ask the platform to prompt.

    Returns whether a prompt was *requested*, which is not the same as shown —
    see the module docstring. The stamp is written whenever we ask, because a
    platform that silently declines to show it has still consumed the attempt as
    far as the user's experience is concerned.
    """
    if not should_request_review():
        return False
    requested = _request_windows(hwnd) if STORE_BUILD else _request_macos()
    if requested:
        _record_prompted()
    return requested


def _request_windows(hwnd: Optional[int]) -> bool:
    """Microsoft Store: StoreContext.RequestRateAndReviewAppAsync."""
    try:
        from app.core.store_entitlement import _associate_window, _await, _store_context
    except Exception:
        return False
    ctx = _store_context()
    if ctx is None:
        return False
    if hwnd:
        # The rate-and-review dialog is modal and needs an owner window, exactly
        # like the purchase dialog. If the association fails we decline rather
        # than risk an ownerless (possibly invisible) dialog.
        try:
            _associate_window(ctx, hwnd)
        except Exception:
            return False
    try:
        _await(ctx.request_rate_and_review_app_async())
    except Exception:
        return False
    return True


def _request_macos() -> bool:
    """Mac App Store: SKStoreReviewController.requestReview().

    Fire and forget — no return value exists to inspect, and the system may
    legitimately show nothing.
    """
    try:
        from app.core.mac_store_entitlement import _storekit
    except Exception:
        return False
    sk = _storekit()
    if sk is None:
        return False
    try:
        sk.SKStoreReviewController.requestReview()
    except Exception:
        return False
    return True
