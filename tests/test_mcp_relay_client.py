"""Relay MCP client — token management, pasteable config, frame classification.

The live end-to-end round-trip (app bridge ↔ relay ↔ AI client) is proven by a
manual spike against the deployed Worker; these are the fast, network-free
invariants that must hold in CI.
"""

import json

import pytest

import app.core.mcp_relay_client as rc


@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(rc.keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(rc.keyring, "set_password",
                        lambda s, u, v: store.__setitem__((s, u), v))
    return store


# --- token ---------------------------------------------------------------

def test_get_or_create_token_persists(fake_keyring):
    first = rc.get_or_create_token()
    assert len(first) >= 20                 # relay requires ≥20 chars
    assert rc.get_or_create_token() == first  # stable across calls


def test_regenerate_token_replaces(fake_keyring):
    first = rc.get_or_create_token()
    second = rc.regenerate_token()
    assert second != first
    assert rc.get_or_create_token() == second  # the new one is now persisted


# --- pasteable AI-client config -----------------------------------------

def test_header_form_config_carries_url_and_bearer():
    cfg = rc.ai_client_config("TOK123")
    entry = cfg["mcpServers"]["easypost-desktop"]
    assert entry["url"] == f"{rc.MCP_RELAY_URL}/mcp"
    assert entry["headers"]["Authorization"] == "Bearer TOK123"


def test_config_json_round_trips():
    assert json.loads(rc.ai_client_config_json("TOK")) == rc.ai_client_config("TOK")


def test_vscode_servers_key_override():
    cfg = rc.ai_client_config("TOK", servers_key="servers")
    assert "servers" in cfg and "mcpServers" not in cfg


def test_path_form_url_embeds_token():
    assert rc.ai_client_url_path_form("TOK") == f"{rc.MCP_RELAY_URL}/t/TOK/mcp"


# --- frame classification (control vs JSON-RPC) --------------------------

@pytest.mark.parametrize("frame", [
    {"type": "relay.hello", "ts": 1},
    {"type": "pong", "ts": 2},
])
def test_control_frames_recognised(frame):
    assert rc._is_control_frame(frame) is True


@pytest.mark.parametrize("frame", [
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 1, "result": {}},
])
def test_jsonrpc_frames_not_treated_as_control(frame):
    assert rc._is_control_frame(frame) is False


def test_lifecycle_starts_stopped():
    client = rc.RelayClient()
    assert client.state == rc.STATE_STOPPED
