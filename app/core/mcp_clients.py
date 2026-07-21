"""Detect installed MCP clients and write our server into their config.

Writing into a file that belongs to another application is not something to do
casually, so this module is built around three rules:

1. **Detect, never assume.** A client counts as installed only if its config
   file or its parent directory already exists. We never create an application
   directory that was not there.
2. **Back up before touching.** Every write leaves a timestamped `.bak`
   alongside the original, so a mistake is recoverable without our help.
3. **Merge, never replace.** The file is read, our single entry is added or
   updated under the client's own key, and everything else is preserved
   byte-for-value. A malformed existing file aborts the write rather than
   being "fixed" by overwriting it.

Config shapes differ. Most clients nest servers under `mcpServers`; VS Code
uses `servers`. That is why the key is part of the client table rather than
hard-coded.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SERVER_KEY = "easypost-desktop"


@dataclass(frozen=True)
class McpClient:
    key: str
    label: str
    config_path: Path
    servers_key: str
    note: str = ""


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))


def known_clients() -> list[McpClient]:
    """Config locations per platform, newest-documented first."""
    home = Path.home()
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        return [
            McpClient("claude", "Claude Desktop", support / "Claude" / "claude_desktop_config.json", "mcpServers"),
            McpClient("cursor", "Cursor", home / ".cursor" / "mcp.json", "mcpServers"),
            McpClient("vscode", "VS Code", support / "Code" / "User" / "mcp.json", "servers"),
            McpClient("windsurf", "Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"),
            McpClient("claude-code", "Claude Code", home / ".claude.json", "mcpServers"),
        ]
    if sys.platform.startswith("win"):
        appdata = _appdata()
        return [
            McpClient("claude", "Claude Desktop", appdata / "Claude" / "claude_desktop_config.json", "mcpServers"),
            McpClient("cursor", "Cursor", home / ".cursor" / "mcp.json", "mcpServers"),
            McpClient("vscode", "VS Code", appdata / "Code" / "User" / "mcp.json", "servers"),
            McpClient("windsurf", "Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"),
            McpClient("claude-code", "Claude Code", home / ".claude.json", "mcpServers"),
        ]
    return [
        McpClient("cursor", "Cursor", home / ".cursor" / "mcp.json", "mcpServers"),
        McpClient("vscode", "VS Code", home / ".config" / "Code" / "User" / "mcp.json", "servers"),
        McpClient("claude-code", "Claude Code", home / ".claude.json", "mcpServers"),
    ]


def is_installed(client: McpClient) -> bool:
    """Present if the config exists, or its directory does.

    Checking the parent too catches a freshly installed client that has not
    yet written a config — a real case, and one where we can still help.
    """
    return client.config_path.exists() or client.config_path.parent.is_dir()


def detect() -> list[McpClient]:
    return [c for c in known_clients() if is_installed(c)]


def server_command() -> tuple[str, list[str]]:
    """How a client should launch our server.

    Frozen builds expose a dedicated executable beside the main app, so the
    client never needs a Python interpreter. From source we fall back to the
    running interpreter and `-m`.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        helper = exe_dir / ("easypost-mcp.exe" if sys.platform.startswith("win") else "easypost-mcp")
        if helper.exists():
            return str(helper), []
        return str(Path(sys.executable)), ["--mcp-server"]
    return sys.executable, ["-m", "app.mcp_server"]


def server_entry() -> dict:
    command, args = server_command()
    return {"command": command, "args": args}


def config_snippet(client: Optional[McpClient] = None) -> str:
    """The JSON a user would paste by hand."""
    key = client.servers_key if client else "mcpServers"
    return json.dumps({key: {SERVER_KEY: server_entry()}}, indent=2)


def read_config(client: McpClient) -> tuple[Optional[dict], Optional[str]]:
    """(parsed, error). A missing file is not an error — it is an empty config."""
    if not client.config_path.exists():
        return {}, None
    try:
        text = client.config_path.read_text(encoding="utf-8").strip()
        return (json.loads(text) if text else {}), None
    except json.JSONDecodeError as exc:
        return None, f"existing config is not valid JSON ({exc.msg}, line {exc.lineno})"
    except OSError as exc:
        return None, f"could not read config: {exc}"


def is_configured(client: McpClient) -> bool:
    data, error = read_config(client)
    if error or data is None:
        return False
    return SERVER_KEY in (data.get(client.servers_key) or {})


def backup(client: McpClient) -> Optional[Path]:
    if not client.config_path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = client.config_path.with_suffix(client.config_path.suffix + f".{stamp}.bak")
    shutil.copy2(client.config_path, target)
    return target


def install(client: McpClient) -> tuple[bool, str]:
    """Merge our entry into the client's config. Returns (ok, message)."""
    data, error = read_config(client)
    if error:
        # Refuse rather than overwrite: the file belongs to another app and may
        # hold configuration the user cannot easily reconstruct.
        return False, f"{client.label}: {error}. Not modified — add the snippet by hand."

    saved = backup(client)
    servers = data.setdefault(client.servers_key, {})
    if not isinstance(servers, dict):
        return False, f"{client.label}: '{client.servers_key}' is not an object. Not modified."
    servers[SERVER_KEY] = server_entry()

    try:
        client.config_path.parent.mkdir(parents=True, exist_ok=True)
        client.config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return False, f"{client.label}: could not write config: {exc}"

    suffix = f" (backup: {saved.name})" if saved else " (new file)"
    return True, f"{client.label}: connected{suffix}. Restart {client.label} to pick it up."


def uninstall(client: McpClient) -> tuple[bool, str]:
    data, error = read_config(client)
    if error or data is None:
        return False, f"{client.label}: {error or 'unreadable'}"
    servers = data.get(client.servers_key) or {}
    if SERVER_KEY not in servers:
        return True, f"{client.label}: nothing to remove."
    backup(client)
    del servers[SERVER_KEY]
    try:
        client.config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return False, f"{client.label}: could not write config: {exc}"
    return True, f"{client.label}: disconnected. Restart {client.label} to apply."


def setup_markdown() -> str:
    """Instructions for a client we could not detect, or for an agent to follow."""
    command, args = server_command()
    return f"""# Connecting Easy-Post Desktop to an AI agent

Easy-Post Desktop speaks the Model Context Protocol (MCP). Point any MCP
client at the server below and the agent can read your shipping data, verify
addresses and shop rates.

## Configuration

Add this to your MCP client's configuration file. Most clients nest servers
under `mcpServers`; VS Code uses `servers` instead.

```json
{json.dumps({"mcpServers": {SERVER_KEY: {"command": command, "args": args}}}, indent=2)}
```

Common locations:

| Client | Config file | Key |
|---|---|---|
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` | `mcpServers` |
| Claude Desktop (Windows) | `%APPDATA%\\Claude\\claude_desktop_config.json` | `mcpServers` |
| Cursor | `~/.cursor/mcp.json` | `mcpServers` |
| VS Code | `<user dir>/Code/User/mcp.json` | `servers` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |
| Claude Code | `~/.claude.json` | `mcpServers` |

Restart the client afterwards.

## What the agent can and cannot do

It can list addresses, shipments, trackers, claims and pickups; verify an
address; look up tariff codes; and shop rates — including a postal-code-only
quote. None of that spends money.

It **cannot** buy a label, buy a pickup, or refund one. Those tools exist, but
they only file a request. A person approves it inside Easy-Post Desktop, and
the details shown there are re-fetched from EasyPost rather than taken from
what the agent said — so an agent cannot misrepresent what it is asking for.
Per-purchase and daily ceilings are refused outright rather than offered for
confirmation.

If you did not expect an approval request to appear, reject it.

## Turning it off

Uncheck **Enable AI agent access** in Easy-Post Desktop, under
Tools > Connect AI agents. The server refuses every call while it is off,
regardless of what remains in a client's configuration.
"""
