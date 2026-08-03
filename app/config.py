"""App-wide paths and constants."""

from pathlib import Path

import platformdirs

APP_NAME = "EasyPost Desktop"
APP_DIR_NAME = "EasyPostDesktop"
KEYRING_SERVICE_NAME = "EasyPostDesktop"

ICON_PATH = Path(__file__).parent / "resources" / "icons" / "app_icon.png"

# The Paddle license gate is enforced ONLY in direct-download builds, which
# bundle this flag file. Store builds (e.g. the Microsoft Store MSIX) omit it,
# so those users are never asked for a license key on top of their store
# purchase. Create app/resources/license_required.flag before packaging a
# direct-download build (CI does this on the macOS leg).
LICENSE_REQUIRED = (Path(__file__).parent / "resources" / "license_required.flag").exists()

# The Microsoft Store build gates production behind a Store "Production unlock"
# add-on instead of a pasted Paddle licence key: test mode is free, production
# needs the in-app purchase. This flag marks that build so the entitlement is
# read from Windows.Services.Store (see app/core/store_entitlement.py) rather
# than the Ed25519 licence path. Mutually exclusive with LICENSE_REQUIRED — a
# build is either the direct-download one or the Store one, never both.
# packaging/build_msix.py writes app/resources/store_build.flag into the MSIX.
STORE_BUILD = (Path(__file__).parent / "resources" / "store_build.flag").exists()

# Where multi-computer and organisation buyers are sent from inside the Store
# build. The Store add-on unlocks a single computer; seat-managed multi-device
# and team licences live on the website, so the Store unlock screen links here.
MULTI_SEAT_URL = "https://easy-post.spencerfields.com/pricing.html"

# The AI-agent (MCP) bridge. Direct-download builds gate it on their own flag
# (created at package time); the Store build supports it too — hence the
# `or STORE_BUILD`.
#
# The Store build reaches parity through two MSIX mechanisms that sidestep the
# original packaging worries. The helper process is not launched from the
# ACL-locked, version-stamped install path: it is exposed as an App Execution
# Alias (`easypost-mcp.exe` on PATH — see packaging/msix/AppxManifest.xml and
# app/core/mcp_clients.server_command), launched with the package's own
# identity so its keyring and app data line up with the GUI. And connecting a
# client never depends on a redirected cross-package write: the copy-paste
# snippet in Tools > Connect AI agents always works, with the auto-write kept
# as a convenience.
MCP_SUPPORTED = (Path(__file__).parent / "resources" / "mcp_supported.flag").exists() or STORE_BUILD

APP_DATA_DIR = Path(platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False))
DATABASE_PATH = APP_DATA_DIR / "easypost_desktop.sqlite3"
SETTINGS_PATH = APP_DATA_DIR / "settings.json"

MODE_TEST = "test"
MODE_PRODUCTION = "production"


def ensure_app_data_dir() -> Path:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DATA_DIR
