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
from typing import Optional

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

# Batch lifecycle events. Creation and purchase are asynchronous and can take
# minutes on a large batch, so these are what let the Batch view react to a
# finished purchase rather than discovering it on its next poll.
_BATCH_EVENTS = ("batch.created", "batch.updated")


def _get_or_create_webhook_secret() -> str:
    secret = keyring.get_password(KEYRING_SERVICE_NAME, _KEYRING_WEBHOOK_SECRET_USERNAME)
    if not secret:
        secret = secrets.token_urlsafe(32)
        keyring.set_password(KEYRING_SERVICE_NAME, _KEYRING_WEBHOOK_SECRET_USERNAME, secret)
    return secret


def _webhook_id_for_mode(settings, mode: str) -> Optional[str]:
    """The webhook registered for this mode, migrating a pre-1.2.0 single id.

    Older builds stored one `webhook_id` with no record of which account it
    belonged to. It is claimed for the mode that is active the first time this
    runs, which is the best available guess and beats repointing a webhook on
    the wrong account.
    """
    by_mode = settings.webhook_ids or {}
    if mode in by_mode:
        return by_mode[mode]
    return settings.webhook_id or None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WebhookManager(QObject):
    state_changed = Signal(str, str)  # (state, detail)
    tracker_updated = Signal(str)  # tracking id, for TrackingView to refresh on
    batch_updated = Signal(str)  # batch id, so BatchView can stop waiting on its timer

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
            mode = client_manager.active_mode
            existing_id = _webhook_id_for_mode(settings, mode)

            webhook = None
            if existing_id:
                try:
                    # The secret is re-sent on every update, not just at
                    # creation. Without it EasyPost keeps whatever secret the
                    # webhook was registered with, so a secret rotated (or a
                    # webhook created on another machine) leaves every incoming
                    # event failing signature validation with a 401 — a push
                    # feature that reports itself as running and silently
                    # delivers nothing.
                    webhook = client.webhook.update(
                        existing_id, url=webhook_url, webhook_secret=secret
                    )
                except Exception:
                    webhook = None
            if webhook is None:
                webhook = client.webhook.create(url=webhook_url, webhook_secret=secret)

            settings.webhook_enabled = True
            settings.webhook_ids = {**(settings.webhook_ids or {}), mode: webhook.id}
            settings.webhook_id = None  # superseded by the per-mode mapping
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
        mode = client_manager.active_mode
        existing_id = _webhook_id_for_mode(settings, mode)
        if existing_id:
            try:
                client_manager.get_client().webhook.delete(existing_id)
            except Exception:
                pass
            # Only this mode's registration is forgotten. Clearing the lot would
            # orphan the other mode's webhook on EasyPost, where it would sit
            # pointed at a tunnel that no longer exists.
            settings.webhook_ids = {
                k: v for k, v in (settings.webhook_ids or {}).items() if k != mode
            }
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
        description = event.get("description") or ""
        result = event.get("result") or {}

        if description == "tracker.updated":
            tracking_id = result.get("id")
            if not tracking_id:
                return
            save_tracker_locally(result)
            self.tracker_updated.emit(tracking_id)
            return

        # Batch creation and purchase are asynchronous and can take minutes on a
        # large batch. Without these the Batch view has nothing to go on but its
        # own timer, which is why it polls every few seconds; a pushed event
        # lets it settle immediately instead.
        if description in _BATCH_EVENTS:
            batch_id = result.get("id")
            if batch_id:
                self.batch_updated.emit(batch_id)


webhook_manager = WebhookManager()
