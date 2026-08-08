"""Local non-secret app preferences (language, label defaults, licence).

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
    # Store build only: when the Microsoft Store last confirmed this account owns
    # the "Production unlock" add-on. Trusted offline for a grace window so a
    # Store outage never revokes production (see app/core/store_entitlement.py).
    store_unlock_confirmed_at: Optional[str] = None
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
    # The newest release the user has dismissed the "update available" banner
    # for (direct-download builds only — see app/core/update_check.py). The
    # banner reappears only once a version *newer than this* ships, so a
    # dismissal is not nagged but a genuinely new release still gets noticed.
    update_dismissed_version: Optional[str] = None
    # Remote AI-agent access over the hosted relay (see app/core/mcp_relay_client.py).
    # Off by default and opt-in: on non-MAS builds the local stdio helper is the
    # default transport, and this opens an *additional* outbound path so a client
    # on another machine — or one that only accepts a URL — can reach the app. On
    # the MAS build the relay is the only transport and tracks mcp_enabled instead,
    # so this flag is not consulted there (see mcp_relay_client.relay_should_run).
    mcp_relay_enabled: bool = False
    # Create Shipment measurement system and weight unit. Defaults keep the
    # original behaviour (inches + ounces, EasyPost-native) so nothing changes
    # for existing users; the last choice made on the form is remembered here.
    # Values are normalised to in/oz before any API call (see app/core/units.py).
    unit_system: str = "imperial"  # "imperial" (in) | "metric" (cm)
    weight_unit: str = "oz"  # imperial: oz|lb ; metric: kg|g


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
