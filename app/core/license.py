"""Offline license verification (Ed25519-signed perpetual licenses).

The app ships with only the *public* key embedded below. License keys are
minted out-of-band with the matching private key (see tools/issue_license.py)
after a Paddle purchase, then pasted into the activation screen. Verification
is fully offline: no server, no network — the signature alone proves the key
was issued by us.

License key format (a compact, copy-pasteable token):

    EPD1.<base64url(payload_json)>.<base64url(signature)>

`payload_json` is signed verbatim, so we verify the exact bytes carried in the
token rather than re-serialising (which avoids any canonicalisation mismatch).
"""

import base64
import json
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.settings import load_settings, save_settings

# Public half of the license signing key. The private half is NOT in the repo.
LICENSE_PUBLIC_KEY_B64 = "344VCmZ52xHf0sTVsAidKz0Dsn0/QfZ+kwcKRGj+GDc="

# Only accept licenses minted for this product / format version.
LICENSE_PRODUCT_ID = "easypost-desktop"
LICENSE_FORMAT_TAG = "EPD1"

# Where customers buy a license (Paddle checkout — set once the product exists).
PADDLE_CHECKOUT_URL = ""


@dataclass
class LicenseInfo:
    """The verified contents of a license key."""

    email: str
    order: str
    product: str
    issued_at: str


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _public_key() -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(LICENSE_PUBLIC_KEY_B64))


def verify_license(key: str) -> Optional[LicenseInfo]:
    """Return the LicenseInfo if `key` is a genuine, well-formed license for
    this product, or None otherwise. Never raises on bad input."""
    if not key:
        return None
    try:
        tag, payload_b64, sig_b64 = key.strip().split(".")
    except (ValueError, AttributeError):
        return None
    if tag != LICENSE_FORMAT_TAG:
        return None
    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        return None
    try:
        _public_key().verify(signature, payload_bytes)
    except InvalidSignature:
        return None
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if payload.get("v") != 1 or payload.get("product") != LICENSE_PRODUCT_ID:
        return None
    return LicenseInfo(
        email=str(payload.get("email", "")),
        order=str(payload.get("order", "")),
        product=str(payload.get("product", "")),
        issued_at=str(payload.get("iat", "")),
    )


def load_active_license() -> Optional[LicenseInfo]:
    """Verify the license stored in settings, if any."""
    return verify_license(load_settings().license_key or "")


def is_licensed() -> bool:
    return load_active_license() is not None


def activate(key: str) -> Optional[LicenseInfo]:
    """Validate `key`; on success persist it and return its info, else None."""
    info = verify_license(key)
    if info is None:
        return None
    settings = load_settings()
    settings.license_key = key.strip()
    save_settings(settings)
    return info


def deactivate() -> None:
    settings = load_settings()
    settings.license_key = None
    save_settings(settings)
