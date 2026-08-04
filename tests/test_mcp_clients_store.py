"""MCP client wiring for the Microsoft Store build.

The Store build reaches AI-agent parity by shipping the MCP helper and exposing
it as an App Execution Alias (see packaging/msix/AppxManifest.xml). The one thing
the app code must get right is the command it hands to MCP clients: the bare
alias `easypost-mcp.exe`, never the versioned, ACL-locked MSIX install path.
"""

import json

import app.core.mcp_clients as mc


def test_store_build_uses_the_execution_alias(monkeypatch):
    monkeypatch.setattr(mc, "STORE_BUILD", True)
    monkeypatch.setattr(mc, "MAS_BUILD", False)  # MAS branch is checked first
    command, args = mc.server_command()
    assert command == "easypost-mcp.exe"
    assert args == []


def test_store_build_snippet_points_at_the_alias(monkeypatch):
    monkeypatch.setattr(mc, "STORE_BUILD", True)
    monkeypatch.setattr(mc, "MAS_BUILD", False)  # MAS branch is checked first
    snippet = json.loads(mc.config_snippet())
    entry = snippet["mcpServers"][mc.SERVER_KEY]
    assert entry == {"command": "easypost-mcp.exe", "args": []}


def test_non_store_source_run_uses_interpreter(monkeypatch):
    """From source (not frozen, not Store) it falls back to `python -m`."""
    monkeypatch.setattr(mc, "STORE_BUILD", False)
    monkeypatch.setattr(mc, "MAS_BUILD", False)
    monkeypatch.setattr(mc.sys, "frozen", False, raising=False)
    command, args = mc.server_command()
    assert args == ["-m", "app.mcp_server"]


# --- Mac App Store build -------------------------------------------------
#
# The MAS build reaches AI clients through the remote relay (not a stdio
# command), so there is no MAS `server_command()`. The one invariant the client
# module must still hold: the sandbox forbids writing another app's config, so
# the auto-write refuses. See MACOS-AI-CONNECTIVITY-BRIEF.md / mcp_relay_client.


def test_mas_build_refuses_config_auto_write(monkeypatch):
    """Under the sandbox, install() must never touch another app's config."""
    monkeypatch.setattr(mc, "MAS_BUILD", True)
    client = mc.known_clients()[0]
    ok, message = mc.install(client)
    assert ok is False
    assert "by hand" in message
