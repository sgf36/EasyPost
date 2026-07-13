"""App-wide paths and constants."""

from pathlib import Path

import platformdirs

APP_NAME = "EasyPost Desktop"
APP_DIR_NAME = "EasyPostDesktop"
KEYRING_SERVICE_NAME = "EasyPostDesktop"

ICON_PATH = Path(__file__).parent / "resources" / "icons" / "app_icon.png"

APP_DATA_DIR = Path(platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False))
DATABASE_PATH = APP_DATA_DIR / "easypost_desktop.sqlite3"
SETTINGS_PATH = APP_DATA_DIR / "settings.json"

MODE_TEST = "test"
MODE_PRODUCTION = "production"

# Stripe Payment Link for optional donations (public URL, not a secret).
DONATION_URL = "https://donate.stripe.com/aFabJ38bEaOq1wMgHl0gw01"


def ensure_app_data_dir() -> Path:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DATA_DIR
