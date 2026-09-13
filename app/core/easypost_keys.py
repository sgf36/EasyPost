"""Determine an EasyPost API key's *true* mode — test or production.

The paid-licence gate must key off what a key actually is, not which input box
it was typed into. Otherwise a customer could paste a production key into the
free "test" field and buy real labels without a licence — EasyPost would honour
it as a genuine production key and the label would be real.

Every object the API returns carries a ``mode`` field ("test" or "production")
reflecting the key that created it. Creating an Address is free and buys
nothing, so it is a safe, no-charge probe: read the ``mode`` off the returned
object and that is the key's true nature. That probe stays the only thing that
can *accept* a key.

Current keys also carry a prefix — ``EZTK`` for test, ``EZAK`` for production —
but EasyPost does not promise every key ever issued has one, so the prefix is
only used to *refuse* a key that is plainly in the wrong field, instantly and
without a network round trip. It never lets a key through on its own.

``check_key`` never raises. It returns the key's mode, or the reason it could
not be confirmed, because "EasyPost refused the key", "EasyPost could not be
reached" and "EasyPost had a fault" each need the customer to do something
different. ``detect_mode`` is the older, reason-free view of the same check.
"""

import threading
import time
from typing import NamedTuple, Optional

import easypost

from app.config import MODE_PRODUCTION, MODE_TEST

# A minimal, syntactically valid US address. Creating it is free and
# non-billable; we only ever read the `mode` field off the result.
_PROBE_ADDRESS = {
    "street1": "417 Montgomery Street",
    "city": "San Francisco",
    "state": "CA",
    "zip": "94104",
    "country": "US",
}

TEST_KEY_PREFIX = "EZTK"
PRODUCTION_KEY_PREFIX = "EZAK"

# Why a key could not be confirmed.
PROBLEM_INVALID_KEY = "invalid_key"        # EasyPost answered and refused the key
PROBLEM_NETWORK = "network"                # EasyPost could not be reached in time
PROBLEM_EASYPOST_ERROR = "easypost_error"  # EasyPost answered with a fault of its own

# The library's default is 60 seconds per request, and name resolution on a
# machine with no connection is not covered by it at all: the first-run screen
# was measured spinning for 84 seconds offline before blaming the key. Someone
# watching "Checking your key…" gives up long before that, so the whole check
# is bounded here, whatever the socket layer does. A healthy check takes under
# a second, so the deadline only ever bites when something is wrong.
REQUEST_TIMEOUT_S = 8
CHECK_DEADLINE_S = 12.0

# EasyPost refusing the key itself. A made-up or revoked key comes back as 403
# "This api key is no longer active", not 401 (observed 2026-09-13).
_INVALID_KEY_STATUSES = {401, 403}


class KeyCheck(NamedTuple):
    mode: Optional[str]     # "test" / "production" when confirmed
    problem: Optional[str]  # one of the PROBLEM_* values when not


def mode_from_prefix(key: str) -> Optional[str]:
    """The mode a key's prefix claims, or None when it has no known prefix.

    A claim, not a verification: use it only to refuse a key early."""
    key = (key or "").strip()
    if key.startswith(TEST_KEY_PREFIX):
        return MODE_TEST
    if key.startswith(PRODUCTION_KEY_PREFIX):
        return MODE_PRODUCTION
    return None


def _classify(exc: BaseException) -> str:
    errors = easypost.errors
    if isinstance(exc, (errors.TimeoutError, errors.HttpError)):
        # The library raises HttpError for every transport failure (DNS,
        # refused connection, TLS) — before any response from EasyPost exists.
        return PROBLEM_NETWORK
    if getattr(exc, "http_status", None) in _INVALID_KEY_STATUSES:
        return PROBLEM_INVALID_KEY
    return PROBLEM_EASYPOST_ERROR


def _probe(key: str) -> KeyCheck:
    try:
        client = easypost.EasyPostClient(key, timeout=REQUEST_TIMEOUT_S)
        obj = client.address.create(**_PROBE_ADDRESS)
    except Exception as exc:
        return KeyCheck(None, _classify(exc))
    mode = getattr(obj, "mode", None)
    if mode in (MODE_TEST, MODE_PRODUCTION):
        return KeyCheck(mode, None)
    # It answered, but not in a shape we recognise: never treat that as safe.
    return KeyCheck(None, PROBLEM_EASYPOST_ERROR)


def check_keys(*keys: str, deadline: float = CHECK_DEADLINE_S) -> list[KeyCheck]:
    """Check several keys at once, all inside one ``deadline`` in seconds.

    Each probe runs on its own daemon thread, so two keys cost one wait rather
    than two, and a probe stuck in name resolution is abandoned instead of
    waited on. Abandoning it is harmless: all it can still do is create a free
    address. An empty key yields ``KeyCheck(None, None)`` — nothing to check.
    """
    results: list[Optional[KeyCheck]] = [None] * len(keys)
    threads = []
    for index, raw in enumerate(keys):
        key = (raw or "").strip()
        if not key:
            results[index] = KeyCheck(None, None)
            continue

        def run(i: int = index, k: str = key) -> None:
            results[i] = _probe(k)

        thread = threading.Thread(target=run, name="easypost-key-check", daemon=True)
        thread.start()
        threads.append(thread)

    ends_at = time.monotonic() + deadline
    for thread in threads:
        thread.join(max(0.0, ends_at - time.monotonic()))
    # A probe still running at the deadline did not reach EasyPost in time.
    return [r if r is not None else KeyCheck(None, PROBLEM_NETWORK) for r in results]


def check_key(key: str, deadline: float = CHECK_DEADLINE_S) -> KeyCheck:
    return check_keys(key, deadline=deadline)[0]


def detect_mode(key: str) -> Optional[str]:
    """Return "test", "production", or None if the key's mode cannot be
    determined (invalid key, or the API could not be reached)."""
    return check_key(key).mode
