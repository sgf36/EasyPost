"""Determine an EasyPost API key's *true* mode — test or production.

The paid-licence gate must key off what a key actually is, not which input box
it was typed into. Otherwise a customer could paste a production key into the
free "test" field and buy real labels without a licence — EasyPost would honour
it as a genuine production key and the label would be real.

EasyPost does not publish a reliable offline prefix to tell the two apart, but
every object the API returns carries a ``mode`` field ("test" or "production")
reflecting the key that created it. Creating an Address is free and buys
nothing, so it is a safe, no-charge probe: read the ``mode`` off the returned
object and that is the key's true nature.

``detect_mode`` never raises. It returns "test" / "production" when it can tell,
or ``None`` when it cannot — an invalid key, or no connection. Callers must
treat ``None`` as "not verified" and refuse to trust the key rather than
assuming it is safe.
"""

from typing import Optional

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


def detect_mode(key: str) -> Optional[str]:
    """Return "test", "production", or None if the key's mode cannot be
    determined (invalid key, or the API could not be reached)."""
    key = (key or "").strip()
    if not key:
        return None
    try:
        client = easypost.EasyPostClient(key)
        obj = client.address.create(**_PROBE_ADDRESS)
        mode = getattr(obj, "mode", None)
        return mode if mode in (MODE_TEST, MODE_PRODUCTION) else None
    except Exception:
        # Invalid key, network failure, API change — anything at all. The
        # caller must not treat an unverified key as safe.
        return None
