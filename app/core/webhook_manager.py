"""Lifecycle manager for the optional webhook push-update feature.

Off by default (AppSettings.webhook_enabled) — starting it opens a local
HTTP port and a public Cloudflare quick tunnel, then registers (or updates)
an EasyPost webhook pointed at that tunnel's URL. Polling
(app/services/tracking.py's refresh_all_trackers) remains the always-on
fallback regardless of this feature's state, since the tunnel can fail to
start or the URL can go stale between launches.
"""

import secrets
import socket

import keyring
from PySide6.QtCore import QObject, Signal

from app.config import KEYRING_SERVICE_NAME
from app.core.client import client_manager
from app.core.http_receiver import WebhookReceiver
from app.core.settings import load_settings, save_settings
from app.core.tunnel import CloudflaredNotInstalledError, CloudflareTunnel, TunnelStartTimeoutError
from app.services.tracking import save_tracker_locally

_KEYRING_WEBHOOK_SECRET_USERNAME = "webhook_secret"

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_ERROR = "error"


def _get_or_create_webhook_secret() -> str:
    secret = keyring.get_password(KEYRING_SERVICE_NAME, _KEYRING_WEBHOOK_SECRET_USERNAME)
    if not secret:
        secret = secrets.token_urlsafe(32)
        keyring.set_password(KEYRING_SERVICE_NAME, _KEYRING_WEBHOOK_SECRET_USERNAME, secret)
    return secret


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WebhookManager(QObject):
    state_changed = Signal(str, str)  # (state, detail)
    tracker_updated = Signal(str)  # tracking id, for TrackingView to refresh on

    def __init__(self) -> None:
        super().__init__()
        self._receiver: WebhookReceiver | None = None
        self._tunnel: CloudflareTunnel | None = None
        self._state = STATE_STOPPED
        self._detail = ""

    @property
    def state(self) -> str:
        return self._state

    @property
    def detail(self) -> str:
        return self._detail

    def _set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        self._detail = detail
        self.state_changed.emit(state, detail)

    def start(self) -> None:
        """Blocking — call from a background thread (see
        app/ui/widgets/async_worker.py's run_async), not the UI thread."""
        self._set_state(STATE_STARTING, "")
        try:
            secret = _get_or_create_webhook_secret()
            settings = load_settings()
            port = settings.webhook_port or _find_free_port()

            self._receiver = WebhookReceiver(webhook_secret=secret, on_event=self._on_event)
            actual_port = self._receiver.start(port=port)

            self._tunnel = CloudflareTunnel()
            public_url = self._tunnel.start(local_port=actual_port)
            webhook_url = f"{public_url}/webhook"

            client = client_manager.get_client()
            webhook = None
            if settings.webhook_id:
                try:
                    webhook = client.webhook.update(settings.webhook_id, url=webhook_url)
                except Exception:
                    webhook = None
            if webhook is None:
                webhook = client.webhook.create(url=webhook_url, webhook_secret=secret)

            settings.webhook_enabled = True
            settings.webhook_id = webhook.id
            settings.webhook_port = actual_port
            save_settings(settings)

            self._set_state(STATE_RUNNING, public_url)
        except (CloudflaredNotInstalledError, TunnelStartTimeoutError) as exc:
            self._teardown()
            self._set_state(STATE_ERROR, str(exc))
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self._teardown()
            self._set_state(STATE_ERROR, str(exc))

    def stop(self) -> None:
        """Explicit user disable — also deletes the EasyPost webhook for a
        clean teardown (merely closing the app without disabling leaves it
        registered; see module docstring)."""
        settings = load_settings()
        if settings.webhook_id:
            try:
                client_manager.get_client().webhook.delete(settings.webhook_id)
            except Exception:
                pass
            settings.webhook_id = None
        settings.webhook_enabled = False
        save_settings(settings)

        self._teardown()
        self._set_state(STATE_STOPPED, "")

    def _teardown(self) -> None:
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None
        if self._receiver is not None:
            self._receiver.stop()
            self._receiver = None

    def _on_event(self, event: dict) -> None:
        """Runs on the HTTP receiver's background thread. Signal emission
        is thread-safe — Qt queues delivery to slots living on the main
        thread automatically."""
        if event.get("description") != "tracker.updated":
            return
        tracker = event.get("result") or {}
        tracking_id = tracker.get("id")
        if not tracking_id:
            return
        save_tracker_locally(tracker)
        self.tracker_updated.emit(tracking_id)


webhook_manager = WebhookManager()
