"""Local non-secret app preferences (language, donation banner dismissal).

Kept separate from credential_store.py since these values aren't sensitive
and don't need OS keyring protection — a plain JSON file is simpler.
"""

import json
from dataclasses import asdict, dataclass
from typing import Optional

from app.config import SETTINGS_PATH, ensure_app_data_dir

DEFAULT_LOCALE = "en"


@dataclass
class AppSettings:
    locale: str = DEFAULT_LOCALE
    donation_banner_dismissed: bool = False
    # Webhook push-update feature (off by default — see app/core/webhook_manager.py).
    webhook_enabled: bool = False
    webhook_id: Optional[str] = None
    webhook_port: Optional[int] = None
    # Activated offline license key (see app/core/license.py). None until activated.
    license_key: Optional[str] = None


def load_settings() -> AppSettings:
    if not SETTINGS_PATH.exists():
        return AppSettings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return AppSettings(**{k: v for k, v in data.items() if k in AppSettings.__dataclass_fields__})
    except Exception:
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    ensure_app_data_dir()
    SETTINGS_PATH.write_text(json.dumps(asdict(settings)), encoding="utf-8")
