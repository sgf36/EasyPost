"""Local HTTP receiver for EasyPost webhook push events.

Binds to 127.0.0.1 only. The Cloudflare tunnel (app/core/tunnel.py)
connects to this local port itself and is what makes it internet
-reachable — the port is never exposed directly on the LAN/internet.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from easypost.util import SignatureVerificationError, validate_webhook

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"


class _WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # silence default request logging to stderr

    def do_POST(self) -> None:
        if self.path != WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length)
        headers = dict(self.headers.items())

        try:
            event = validate_webhook(body, headers, self.server.webhook_secret)
        except SignatureVerificationError:
            self.send_response(401)
            self.end_headers()
            return
        except (json.JSONDecodeError, ValueError):
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()

        try:
            self.server.on_event(event)
        except Exception:
            logger.exception("Unhandled error processing webhook event")


class WebhookReceiver:
    """Owns the local ThreadingHTTPServer and its background thread."""

    def __init__(self, webhook_secret: str, on_event: Callable[[dict], None]) -> None:
        self._webhook_secret = webhook_secret
        self._on_event = on_event
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, port: int = 0) -> int:
        """Starts the server on `port` (0 = OS-assigned free port) and
        returns the actual bound port."""
        httpd = ThreadingHTTPServer(("127.0.0.1", port), _WebhookHandler)
        httpd.webhook_secret = self._webhook_secret
        httpd.on_event = self._on_event
        self._httpd = httpd

        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return httpd.server_address[1]

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None
