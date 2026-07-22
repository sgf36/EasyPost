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

# The AI-agent (MCP) bridge is likewise direct-download only for now, and
# gated on its own flag rather than reusing LICENSE_REQUIRED — they answer
# different questions and will not always move together.
#
# Connecting an agent means an external application launches a helper process
# from inside this install and writes to config files belonging to other apps.
# Under MSIX both of those run into packaging constraints: the install path is
# virtualised, and writes outside the package are redirected. Rather than
# discover that during Store certification, the Store build ships without the
# feature and says so plainly in the UI.
MCP_SUPPORTED = (Path(__file__).parent / "resources" / "mcp_supported.flag").exists()

APP_DATA_DIR = Path(platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False))
DATABASE_PATH = APP_DATA_DIR / "easypost_desktop.sqlite3"
SETTINGS_PATH = APP_DATA_DIR / "settings.json"

MODE_TEST = "test"
MODE_PRODUCTION = "production"


def ensure_app_data_dir() -> Path:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DATA_DIR
