"""Outbound MCP-over-WebSocket client for the remote relay.

On builds that cannot expose a local stdio helper to an AI client — chiefly the
sandboxed Mac App Store build — the app reaches AI clients through the deployed
relay (server/mcp-relay-worker). The app dials an **outbound** WebSocket to the
relay, then serves MCP over it: the app stays the MCP *server* (it executes every
tool locally against its own keyring + database), while the relay is a dumb
transport bridge that never sees the EasyPost key and never runs a tool. Buying
still requires in-app human approval (app/core/mcp_approvals.py), unchanged.

Design mirrors app/core/webhook_manager.py: a QObject with a `state_changed`
signal, a secret in the keyring, a blocking-ish lifecycle driven from a
background thread, and "degrade safely, never raise" discipline. Every heavy or
platform-specific import (websockets, the mcp SDK, the tool registry) is lazy and
guarded so importing this module is free and safe on any build.

The transport framing mirrors mcp.server.stdio: each relay text frame that is a
JSON-RPC message becomes a `SessionMessage` fed to the SDK server's read stream;
each message the server writes is serialised back onto the socket. Relay control
frames (`{"type": "relay.hello"|"pong"}`) are handled out of band.
"""

from __future__ import annotations

import json
import secrets
import threading

import keyring
from PySide6.QtCore import QObject, Signal

from app.config import KEYRING_SERVICE_NAME, MCP_RELAY_URL

_KEYRING_RELAY_TOKEN_USERNAME = "mcp_relay_token"

STATE_STOPPED = "stopped"
STATE_CONNECTING = "connecting"
STATE_RUNNING = "running"
STATE_ERROR = "error"

_SERVER_KEY = "easypost-desktop"
_BACKOFF_START = 1.0
_BACKOFF_MAX = 30.0
_PING_INTERVAL = 30.0


# --- pairing token -------------------------------------------------------

def get_or_create_token() -> str:
    """The per-app pairing token. A leaked token exposes read/rate-shop access
    to this account's shipping data (never spend) while the app is online, and is
    revocable via regenerate_token()."""
    token = keyring.get_password(KEYRING_SERVICE_NAME, _KEYRING_RELAY_TOKEN_USERNAME)
    if not token:
        token = secrets.token_urlsafe(32)
        keyring.set_password(KEYRING_SERVICE_NAME, _KEYRING_RELAY_TOKEN_USERNAME, token)
    return token


def regenerate_token() -> str:
    """Revoke the current pairing (any connected AI client stops working) and
    mint a new token. The caller should restart the relay connection after."""
    token = secrets.token_urlsafe(32)
    keyring.set_password(KEYRING_SERVICE_NAME, _KEYRING_RELAY_TOKEN_USERNAME, token)
    return token


# --- AI-client configuration the user pastes -----------------------------

def _mcp_url(token: str) -> str:
    return f"{MCP_RELAY_URL}/mcp"


def ai_client_config(token: str, servers_key: str = "mcpServers") -> dict:
    """Header-form config (preferred) an AI client pastes to reach this app."""
    return {
        servers_key: {
            _SERVER_KEY: {
                "url": _mcp_url(token),
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }


def ai_client_config_json(token: str, servers_key: str = "mcpServers") -> str:
    return json.dumps(ai_client_config(token, servers_key), indent=2)


def ai_client_url_path_form(token: str) -> str:
    """URL form for clients that cannot set an Authorization header."""
    return f"{MCP_RELAY_URL}/t/{token}/mcp"


def _is_control_frame(obj: object) -> bool:
    """A relay control frame (relay.hello / pong) rather than a JSON-RPC message.
    JSON-RPC always carries a "jsonrpc" member; control frames carry "type"."""
    return isinstance(obj, dict) and "jsonrpc" not in obj and "type" in obj


# --- lifecycle manager ---------------------------------------------------

class RelayClient(QObject):
    """Owns the background thread that keeps the outbound relay connection up and
    serves MCP over it. Start/stop are non-blocking; the work runs on an internal
    daemon thread running its own asyncio loop."""

    state_changed = Signal(str, str)  # (state, detail)

    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
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
        """Begin (re)connecting to the relay in the background. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._set_state(STATE_CONNECTING, "")
        self._thread = threading.Thread(
            target=self._thread_main, name="mcp-relay", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the connection loop to exit and wait briefly for the thread."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self._set_state(STATE_STOPPED, "")

    # -- background thread -------------------------------------------------

    def _thread_main(self) -> None:
        try:
            import anyio

            anyio.run(self._serve)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self._set_state(STATE_ERROR, str(exc))

    async def _serve(self) -> None:
        """Reconnect-with-backoff loop; each iteration bridges one WS session."""
        import anyio
        import websockets

        token = get_or_create_token()
        ws_url = f"{MCP_RELAY_URL.replace('https://', 'wss://').replace('http://', 'ws://')}/t/{token}/connect"
        backoff = _BACKOFF_START

        while not self._stop.is_set():
            try:
                async with websockets.connect(ws_url, open_timeout=15) as ws:
                    backoff = _BACKOFF_START  # a good connection resets backoff
                    await self._bridge_session(ws)
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    break
                self._set_state(STATE_ERROR, str(exc))
            # Wait out the backoff, but wake promptly on stop.
            with anyio.move_on_after(backoff):
                while not self._stop.is_set():
                    await anyio.sleep(0.2)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _bridge_session(self, ws) -> None:
        """Pump one WebSocket session onto the SDK server's memory streams."""
        import anyio
        import mcp.types as types
        from mcp.shared.message import SessionMessage

        import app.mcp_server as srv  # lazy: pulls the whole tool registry

        srv.init_db()
        lowlevel = srv.mcp._mcp_server
        init_opts = lowlevel.create_initialization_options()

        read_w, read_r = anyio.create_memory_object_stream(0)
        write_w, write_r = anyio.create_memory_object_stream(0)

        async def ws_to_server() -> None:
            async with read_w:
                async for raw in ws:
                    try:
                        obj = json.loads(raw)
                    except Exception:
                        continue
                    if _is_control_frame(obj):
                        continue
                    try:
                        msg = types.JSONRPCMessage.model_validate(obj)
                    except Exception as exc:  # malformed → let the server error it
                        await read_w.send(exc)
                        continue
                    await read_w.send(SessionMessage(msg))

        async def server_to_ws() -> None:
            async with write_r:
                async for sm in write_r:
                    await ws.send(
                        sm.message.model_dump_json(by_alias=True, exclude_none=True)
                    )

        async def keepalive() -> None:
            while not self._stop.is_set():
                await anyio.sleep(_PING_INTERVAL)
                try:
                    await ws.send(json.dumps({"type": "ping"}))
                except Exception:
                    return

        async def watch_stop() -> None:
            while not self._stop.is_set():
                await anyio.sleep(0.25)
            await ws.close()

        self._set_state(STATE_RUNNING, MCP_RELAY_URL)
        async with anyio.create_task_group() as tg:
            tg.start_soon(ws_to_server)
            tg.start_soon(server_to_ws)
            tg.start_soon(keepalive)
            tg.start_soon(watch_stop)
            await lowlevel.run(read_r, write_w, init_opts)
            tg.cancel_scope.cancel()


relay_client = RelayClient()
