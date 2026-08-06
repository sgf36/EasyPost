"""Pair the Easy-Post Mobile Companion with this desktop.

The desktop shows a QR code the phone scans. For security the QR carries only a
one-time pairing token — never the production key. The desktop registers that
token together with the production key against the proxy, which encrypts the key
under a fresh key it then hands to the phone and forgets: the proxy stores only
ciphertext it cannot read on its own. See MOBILE-COMPANION-BUILD-BRIEF.md and
server/easypost-mobile-proxy.
"""

import json
import uuid

import requests

from app.config import PAIR_PROXY_URL
from app.core.credential_store import load_credentials
from app.core.settings import load_settings


class PairingError(Exception):
    """A pairing attempt failed. `reason` is a short, translatable code the UI
    maps to a friendly message."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def production_key() -> str | None:
    return load_credentials().production_key


def register_pairing(timeout: float = 12.0) -> dict:
    """Mint a one-time pairing token, register it (with the production key and
    licence) against the proxy, and return the payload to render as a QR.

    Returns ``{"pairing_token", "proxy_url", "qr_payload"}`` where ``qr_payload``
    is the compact JSON string the phone scans. Raises :class:`PairingError`
    with a reason code (``no_production_key``, ``no_license``,
    ``invalid_license``, ``network`` or ``server``) on any failure.
    """
    key = production_key()
    if not key:
        raise PairingError("no_production_key")
    license_key = (load_settings().license_key or "").strip()
    if not license_key:
        raise PairingError("no_license")

    token = uuid.uuid4().hex
    try:
        resp = requests.post(
            f"{PAIR_PROXY_URL}/pair/register",
            json={"pairing_token": token, "easypost_key": key, "license": license_key},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise PairingError("network") from exc

    if resp.status_code == 403:
        raise PairingError("invalid_license")
    if resp.status_code != 200:
        raise PairingError("server")

    # Compact keys keep the QR small and quick to scan: t = token, u = proxy URL.
    qr_payload = json.dumps({"t": token, "u": PAIR_PROXY_URL}, separators=(",", ":"))
    return {"pairing_token": token, "proxy_url": PAIR_PROXY_URL, "qr_payload": qr_payload}
