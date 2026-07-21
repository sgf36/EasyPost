"""Seat activation: which computers a licence key has been used on.

Tiers differ only in how many computers one key may run on, so something has
to count. That count cannot live purely on the customer's machine, which means
the app now talks to a server it previously never needed. The whole design here
is about keeping that intrusion as small and as recoverable as possible.

**The network is touched once.** Activation asks the server for a *receipt* —
an Ed25519-signed statement that this computer holds one of the licence's seats
— and that receipt is verified offline on every launch thereafter. A customer
who activates and then never reconnects keeps working indefinitely. There is no
recurring check-in, no heartbeat, and nothing to fail at launch.

**The server never learns which computer it is.** What gets sent is
`HMAC-SHA256(licence_key, machine_id)`. The machine identifier itself never
leaves the device. Because the licence key is the HMAC key, the same computer
under two different licences produces two unrelated hashes, so devices cannot
be correlated across customers.

**Possession of the key is proved, not asserted.** Requests carry an HMAC over
the request fields, again keyed by the licence key. Someone who has only seen
an order id cannot burn a stranger's seats.

**An outage of ours is never the customer's problem.** If activation cannot
reach the server, the app grants itself a time-limited grace and retries in the
background. Being unable to bill someone is our failure; locking them out of
software they have paid for would be a worse one.

The server verifies the licence signature itself rather than consulting a list
of known orders. That keeps minting and activation decoupled: a complimentary
key minted by hand with tools/issue_license.py activates exactly like a
purchased one, with no record to sync anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config import ensure_app_data_dir
from app.core.license import LICENSE_PUBLIC_KEY_B64, LicenseInfo
from app.core.settings import load_settings, save_settings

RECEIPT_TAG = "EPDR1"

# Where the app asks for a seat. Same Worker as the licence webhook.
ACTIVATION_BASE_URL = "https://easypost-license-webhook.sgf36.workers.dev"

# How long the app will run offline when our server could not be reached at
# activation time. Long enough to cover an outage or a flight, short enough that
# it is a grace period rather than a way around activation.
GRACE_DAYS = 14

# Network calls here are the only ones a launch could ever block on, so they get
# a hard, short ceiling. Failure is a fallback path, not an error state.
REQUEST_TIMEOUT_SECONDS = 8


class ActivationError(Exception):
    """Activation failed for a reason worth showing the user."""


class SeatsExhausted(ActivationError):
    """The licence is already on as many computers as it allows."""

    def __init__(self, message: str, devices: list[dict]):
        super().__init__(message)
        self.devices = devices


class LicenseRevoked(ActivationError):
    """The key was refunded or withdrawn."""


class ActivationUnreachable(ActivationError):
    """The server could not be reached. Always recoverable — never fatal."""


@dataclass
class Receipt:
    """A verified statement that this computer holds a seat."""

    order: str
    device: str
    tier: str
    seats: int
    issued_at: str
    expires_at: str
    provisional: bool = False

    @property
    def is_expired(self) -> bool:
        return _parse_iso(self.expires_at) <= _now()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        # An unparseable expiry is treated as already past: fail closed on the
        # date, never on the signature.
        return datetime.min.replace(tzinfo=timezone.utc)


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


# --------------------------------------------------------------------------
# Machine identity
# --------------------------------------------------------------------------

def _windows_machine_id() -> Optional[str]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as handle:
            value, _ = winreg.QueryValueEx(handle, "MachineGuid")
            return str(value) or None
    except (ImportError, OSError):
        return None


def _macos_machine_id() -> Optional[str]:
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "IOPlatformUUID" in line and '"' in line:
            return line.rsplit('"', 2)[-2] or None
    return None


def _linux_machine_id() -> Optional[str]:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    return None


def _fallback_machine_id() -> str:
    """A random id kept beside the app's own data.

    Only reached when the OS has no stable identifier to offer. Losing it looks
    like a new computer, which costs the customer a seat — hence the release
    and auto-reclaim paths, and hence trying the OS first.
    """
    path = ensure_app_data_dir() / "device-id"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    generated = uuid.uuid4().hex
    try:
        path.write_text(generated, encoding="utf-8")
    except OSError:
        # Read-only install or a locked profile: still return something usable
        # for this session rather than refusing to run.
        pass
    return generated


def machine_id() -> str:
    """A stable identifier for this computer. Never leaves the device."""
    if sys.platform.startswith("win"):
        found = _windows_machine_id()
    elif sys.platform == "darwin":
        found = _macos_machine_id()
    else:
        found = _linux_machine_id()
    return found or _fallback_machine_id()


def device_hash(license_key: str) -> str:
    """What the server sees instead of the machine id.

    Keyed by the licence, so the same computer under two licences is two
    unrelated values and no cross-customer correlation is possible.
    """
    digest = hmac.new(
        license_key.strip().encode("utf-8"),
        machine_id().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]


def default_device_label() -> str:
    """A human-readable name for the device list, so a seat can be released
    without the customer having to work out which hash is which."""
    name = platform.node() or "This computer"
    system = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
    return f"{name} ({system})"[:64]


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------

def _public_key() -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(LICENSE_PUBLIC_KEY_B64))


def verify_receipt(token: str, expected_device: str) -> Optional[Receipt]:
    """Return the Receipt if `token` is genuine and issued to this computer.

    Bound to the device hash so a receipt copied to another machine is inert:
    it verifies as a signature but names a device that is not this one.
    """
    if not token:
        return None
    try:
        tag, payload_b64, sig_b64 = token.strip().split(".")
    except (ValueError, AttributeError):
        return None
    if tag != RECEIPT_TAG:
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
    if payload.get("v") != 1 or payload.get("device") != expected_device:
        return None
    return Receipt(
        order=str(payload.get("order", "")),
        device=str(payload.get("device", "")),
        tier=str(payload.get("tier", "")),
        seats=int(payload.get("seats", 0) or 0),
        issued_at=str(payload.get("iat", "")),
        expires_at=str(payload.get("exp", "")),
    )


def _grace_receipt(info: LicenseInfo, device: str, until: datetime) -> Receipt:
    return Receipt(
        order=info.order,
        device=device,
        tier=info.tier,
        seats=info.seats,
        issued_at=_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        provisional=True,
    )


def current_receipt(info: LicenseInfo) -> Optional[Receipt]:
    """This computer's seat, verified offline. None if it holds no valid one."""
    settings = load_settings()
    device = device_hash(settings.license_key or "")

    receipt = verify_receipt(settings.activation_receipt or "", device)
    if receipt is not None and not receipt.is_expired:
        return receipt

    # No signed receipt: fall back to a grace window if one is still running.
    if settings.activation_grace_until:
        until = _parse_iso(settings.activation_grace_until)
        if until > _now():
            return _grace_receipt(info, device, until)
    return None


def ensure_seat(info: LicenseInfo) -> bool:
    """Make sure this computer holds a seat, claiming one only if it has none.

    The upgrade path: someone who bought before tiers existed has a valid key
    and no receipt, and should not be sent back to the activation screen. It is
    also the only place the app reaches the network without being asked, which
    is why it returns early the moment a valid receipt is present.

    Never raises. A failure here means "could not confirm", and the caller
    decides — it must not be the reason an app that was working stops working.
    """
    if current_receipt(info) is not None:
        return True
    settings = load_settings()
    try:
        activate_device(settings.license_key or "", info)
        return True
    except ActivationUnreachable:
        start_grace()
        return True
    except ActivationError:
        return False


def start_grace() -> datetime:
    """Let the app run while our server is unreachable, and record until when."""
    until = _now() + timedelta(days=GRACE_DAYS)
    settings = load_settings()
    settings.activation_grace_until = until.strftime("%Y-%m-%dT%H:%M:%SZ")
    save_settings(settings)
    return until


def store_receipt(token: str) -> None:
    settings = load_settings()
    settings.activation_receipt = token
    settings.activation_grace_until = None  # a real receipt supersedes any grace
    save_settings(settings)


def clear_receipt() -> None:
    settings = load_settings()
    settings.activation_receipt = None
    settings.activation_grace_until = None
    save_settings(settings)


# --------------------------------------------------------------------------
# Talking to the activation service
# --------------------------------------------------------------------------

def _sign_request(license_key: str, *fields: str) -> str:
    """Proof that the caller holds the key, without transmitting it."""
    message = "|".join(fields).encode("utf-8")
    return hmac.new(license_key.strip().encode("utf-8"), message, hashlib.sha256).hexdigest()


def _post(path: str, body: dict) -> dict:
    import requests  # local import: keeps app start-up off the network stack

    try:
        response = requests.post(
            f"{ACTIVATION_BASE_URL}{path}",
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — any transport failure is the same case
        raise ActivationUnreachable(str(exc)) from exc

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code == 409:
        raise SeatsExhausted(
            data.get("error") or "This licence is already on all its computers.",
            data.get("devices") or [],
        )
    if response.status_code == 403:
        raise LicenseRevoked(data.get("error") or "This licence is no longer valid.")
    if response.status_code >= 500:
        # Our fault, so treat it exactly like being offline: grace, not refusal.
        raise ActivationUnreachable(f"server returned {response.status_code}")
    if not response.ok:
        raise ActivationError(data.get("error") or f"Activation failed ({response.status_code}).")
    return data


def activate_device(license_key: str, info: LicenseInfo, label: str = "") -> Receipt:
    """Claim a seat for this computer and store the resulting receipt.

    Raises SeatsExhausted (with the device list, so the user can release one),
    LicenseRevoked, or ActivationUnreachable. The caller decides what a failure
    means; this function never silently downgrades.
    """
    device = device_hash(license_key)
    label = (label or default_device_label())[:64]
    stamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")

    data = _post("/activate", {
        "license": license_key.strip(),
        "device": device,
        "label": label,
        "ts": stamp,
        "proof": _sign_request(license_key, info.order, device, stamp),
    })

    token = str(data.get("receipt") or "")
    receipt = verify_receipt(token, device)
    if receipt is None:
        # A receipt we cannot verify is worse than none: refuse it rather than
        # storing something that will fail confusingly at the next launch.
        raise ActivationError("The server returned a receipt that failed verification.")
    store_receipt(token)

    settings = load_settings()
    settings.device_label = label
    save_settings(settings)
    return receipt


def list_devices(license_key: str, info: LicenseInfo) -> list[dict]:
    """Which computers this licence is on. Used to choose one to release."""
    device = device_hash(license_key)
    stamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _post("/devices", {
        "license": license_key.strip(),
        "device": device,
        "ts": stamp,
        "proof": _sign_request(license_key, info.order, device, stamp),
    })
    return data.get("devices") or []


def release_device(license_key: str, info: LicenseInfo, target: str = "") -> None:
    """Free a seat — this computer by default, or another by its device hash."""
    device = device_hash(license_key)
    target = target or device
    stamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    _post("/deactivate", {
        "license": license_key.strip(),
        "device": device,
        "target": target,
        "ts": stamp,
        "proof": _sign_request(license_key, info.order, device, stamp, target),
    })
    if target == device:
        clear_receipt()
