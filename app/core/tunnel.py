"""Cloudflare Quick Tunnel wrapper (cloudflared).

Zero-signup: cloudflared creates an anonymous https://*.trycloudflare.com
URL pointing at a local port — no Cloudflare account needed. Tradeoffs
(documented to the user in Settings/README): not a guaranteed-uptime
service, and the URL changes every time the tunnel is (re)started.
"""

import platform
import re
import shutil
import subprocess
import threading
import time
from typing import Optional

_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

_INSTALL_INSTRUCTIONS = {
    "Windows": "winget install --id Cloudflare.cloudflared",
    "Darwin": "brew install cloudflared",
}
_LINUX_INSTRUCTIONS = (
    "See https://pkg.cloudflare.com/index.html for your distro's package, "
    "or download a binary from https://github.com/cloudflare/cloudflared/releases"
)


class CloudflaredNotInstalledError(RuntimeError):
    pass


class TunnelStartTimeoutError(RuntimeError):
    pass


def install_instructions() -> str:
    return _INSTALL_INSTRUCTIONS.get(platform.system(), _LINUX_INSTRUCTIONS)


class CloudflareTunnel:
    """One quick-tunnel process per instance. Not reused across restarts —
    call start() again for a fresh URL after stop()."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None

    def start(self, local_port: int, timeout: float = 20.0) -> str:
        binary = shutil.which("cloudflared")
        if not binary:
            raise CloudflaredNotInstalledError(
                f"cloudflared not found on PATH. Install it with: {install_instructions()}"
            )

        self._process = subprocess.Popen(
            [binary, "tunnel", "--url", f"http://127.0.0.1:{local_port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        url_holder: dict[str, str] = {}

        def _drain_output() -> None:
            # Keep reading for the process's whole life so cloudflared's
            # stdout pipe never fills up and blocks it, even after we've
            # already captured the URL from an earlier line.
            for line in self._process.stdout:
                if "url" not in url_holder:
                    match = _URL_PATTERN.search(line)
                    if match:
                        url_holder["url"] = match.group(0)

        self._reader_thread = threading.Thread(target=_drain_output, daemon=True)
        self._reader_thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "url" in url_holder:
                return url_holder["url"]
            if self._process.poll() is not None:
                raise RuntimeError("cloudflared exited before producing a tunnel URL")
            time.sleep(0.2)

        self.stop()
        raise TunnelStartTimeoutError(
            "Timed out waiting for cloudflared to report a tunnel URL"
        )

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        self._reader_thread = None
