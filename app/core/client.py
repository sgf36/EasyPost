"""EasyPost client access, gated by the active test/production mode."""

from typing import Optional

import easypost

from app.config import MODE_PRODUCTION, MODE_TEST
from app.core.credential_store import Credentials, load_credentials
from app.core.license import production_allowed


class ClientNotConfigured(RuntimeError):
    """Raised when no API key is stored for the active mode."""


class ProductionLicenseRequired(RuntimeError):
    """Raised when production mode is requested but the build is not licensed.

    The UI keeps a user from reaching this in normal use (production mode can
    only be enabled once a licence is active); it is the last-resort backstop so
    that no production EasyPost client is ever handed out unlicensed, whatever
    path the caller took."""


class ClientManager:
    """Holds the current Credentials and hands out an EasyPostClient for
    whichever mode (test/production) is active. Re-reads credentials from
    disk on demand so a mode switch in Settings takes effect immediately.
    """

    def __init__(self) -> None:
        self._credentials: Credentials = load_credentials()

    def reload(self) -> None:
        self._credentials = load_credentials()

    @property
    def credentials(self) -> Credentials:
        return self._credentials

    @property
    def active_mode(self) -> str:
        return self._credentials.active_mode

    def is_production(self) -> bool:
        return self.active_mode == MODE_PRODUCTION

    def get_client(self) -> easypost.EasyPostClient:
        # Production — real labels, real money — requires a licence in
        # direct-download builds. Test mode is always free. Keys are verified to
        # their true mode when stored (see easypost_keys.detect_mode), so the
        # test slot cannot smuggle in a production key.
        if self.is_production() and not production_allowed():
            raise ProductionLicenseRequired(
                "A licence is required to ship in EasyPost production mode. "
                "Test mode is free."
            )
        key = self._credentials.active_key()
        if not key:
            raise ClientNotConfigured(
                f"No API key stored for '{self.active_mode}' mode. "
                "Add one in Settings."
            )
        return easypost.EasyPostClient(key)

    def get_client_for(self, mode: str) -> Optional[easypost.EasyPostClient]:
        key = self._credentials.key_for_mode(mode)
        return easypost.EasyPostClient(key) if key else None


# Process-wide singleton; the GUI is single-instance per user session.
client_manager = ClientManager()
