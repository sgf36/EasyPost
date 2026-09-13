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
from app.core.settings import load_settings, save_settings


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


def revoke_all_phones(easypost_key: str | None = None, timeout: float = 12.0) -> int:
    """Revoke every phone paired to a production EasyPost key and return how
    many the proxy cut off.

    ``easypost_key`` defaults to the stored production key. Settings passes the
    key it is about to replace instead, because the proxy matches phones by a
    hash of the key they were paired with, and once that key is discarded
    nothing on this computer can name them any more.

    The key is the proof of ownership: whoever holds it can already do more
    than any phone. The licence goes too whenever there is one, because phones
    paired before the proxy recorded key hashes can be reached only through the
    licence order. Store builds have no licence and still reach every phone
    paired since.

    Raises :class:`PairingError` with ``no_production_key``,
    ``invalid_license``, ``network`` or ``server``.
    """
    key = easypost_key or production_key()
    if not key:
        raise PairingError("no_production_key")
    body = {"easypost_key": key}
    license_key = (load_settings().license_key or "").strip()
    if license_key:
        body["license"] = license_key

    try:
        resp = requests.post(f"{PAIR_PROXY_URL}/pair/revoke-all", json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise PairingError("network") from exc

    # The proxy answers 403 only for a licence that is present and fails
    # verification. A 400 means this client sent the wrong body, which the user
    # cannot fix, so it reads as a service fault rather than their mistake.
    if resp.status_code == 403:
        raise PairingError("invalid_license")
    if resp.status_code != 200:
        raise PairingError("server")
    try:
        revoked = resp.json().get("revoked")
    except (ValueError, AttributeError) as exc:
        raise PairingError("server") from exc
    # A count is what tells the user it worked. Anything else — a proxy
    # rollback, a captive portal answering 200 — must not read as success.
    if isinstance(revoked, bool) or not isinstance(revoked, int) or revoked < 0:
        raise PairingError("server")
    return revoked


# Whether this desktop may have paired a phone under the current production key.
#
# The proxy is the only thing that knows which phones exist, and asking it costs
# a round trip on every key change. But the desktop does know when it has never
# registered a pairing under this key, which is most installs, and then there is
# nothing to revoke and no reason to make a call. None means "not recorded":
# anything that already held a production key before this was tracked, where
# only the proxy can answer.
#
# These run on the UI thread, never inside a worker: settings.json is rewritten
# whole, so a write from another thread could silently undo a save the UI made
# at the same moment.


def phones_may_be_paired() -> bool:
    return load_settings().mobile_phones_may_be_paired is not False


def record_pairing_registered() -> None:
    _record_phones_may_be_paired(True)


def record_no_phones_paired() -> None:
    _record_phones_may_be_paired(False)


def _record_phones_may_be_paired(value: bool) -> None:
    settings = load_settings()
    if settings.mobile_phones_may_be_paired is value:
        return
    settings.mobile_phones_may_be_paired = value
    save_settings(settings)
