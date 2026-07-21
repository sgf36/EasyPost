"""Local non-secret app preferences (language, donation banner dismissal).

Kept separate from credential_store.py since these values aren't sensitive
and don't need OS keyring protection — a plain JSON file is simpler.
"""

import json
from dataclasses import asdict, dataclass
from typing import Optional

from app.config import SETTINGS_PATH, ensure_app_data_dir
from app.core.label_options import DEFAULT_LABEL_FORMAT, DEFAULT_LABEL_SIZE

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
    # Signed proof that this computer holds one of the licence's seats. Verified
    # offline on every launch, so activation touches the network exactly once.
    activation_receipt: Optional[str] = None
    # Set only when activation could not reach the server; a time-limited grace
    # so an outage of ours never locks a paying customer out of their own app.
    activation_grace_until: Optional[str] = None
    device_label: Optional[str] = None
    # Preferred printed-label format/size (see app/core/label_options.py).
    # Applies to every shipment created, since EasyPost only honours
    # label_size at shipment-creation time.
    label_format: str = DEFAULT_LABEL_FORMAT
    label_size: str = DEFAULT_LABEL_SIZE
    # AI-agent (MCP) bridge — off until explicitly enabled. The ceilings are
    # deliberately conservative defaults: an agent that has been prompt-injected
    # should hit a wall long before it can do real damage, and raising them is
    # a decision the user makes knowingly. 0 means "no limit", which is why
    # neither defaults to 0.
    mcp_enabled: bool = False
    mcp_allow_spending: bool = False
    mcp_max_purchase: float = 50.0
    mcp_daily_limit: float = 200.0


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
