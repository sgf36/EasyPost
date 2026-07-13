"""Cross-platform local storage for EasyPost API keys.

Keys are stored via the OS-native credential vault through the `keyring`
library: Windows Credential Manager, macOS Keychain, or the Linux Secret
Service, depending on platform. Only the same OS user account on the same
machine can read them back.
"""

import json
from dataclasses import asdict, dataclass
from typing import Optional

import keyring

from app.config import KEYRING_SERVICE_NAME, MODE_PRODUCTION, MODE_TEST

_KEYRING_USERNAME = "credentials"


@dataclass
class Credentials:
    test_key: Optional[str] = None
    production_key: Optional[str] = None
    active_mode: str = MODE_TEST

    def key_for_mode(self, mode: str) -> Optional[str]:
        return self.production_key if mode == MODE_PRODUCTION else self.test_key

    def active_key(self) -> Optional[str]:
        return self.key_for_mode(self.active_mode)

    def has_mode(self, mode: str) -> bool:
        return bool(self.key_for_mode(mode))


def load_credentials() -> Credentials:
    try:
        raw = keyring.get_password(KEYRING_SERVICE_NAME, _KEYRING_USERNAME)
        if not raw:
            return Credentials()
        return Credentials(**json.loads(raw))
    except Exception:
        # Corrupt entry, foreign-machine restore, or no OS keyring backend
        # available: treat as unset rather than crashing the app on launch.
        return Credentials()


def save_credentials(credentials: Credentials) -> None:
    keyring.set_password(
        KEYRING_SERVICE_NAME, _KEYRING_USERNAME, json.dumps(asdict(credentials))
    )


def clear_credentials() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, _KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass
