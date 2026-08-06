"""Pairing service: register a one-time token with the proxy, never leaking the
production key into the QR payload."""

import json
from types import SimpleNamespace

import pytest

from app.services import mobile_pairing as mp
from app.services.mobile_pairing import PairingError, register_pairing


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def ready(monkeypatch):
    """A licensed install with a production key configured."""
    monkeypatch.setattr(mp, "load_credentials", lambda: SimpleNamespace(production_key="EZTKprod_secret"))
    monkeypatch.setattr(mp, "load_settings", lambda: SimpleNamespace(license_key="EPD1.aaa.bbb"))


def test_register_returns_token_qr_without_key(monkeypatch, ready):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _Resp(200)

    monkeypatch.setattr(mp.requests, "post", fake_post)
    result = register_pairing()

    assert captured["url"].endswith("/pair/register")
    # The key IS sent to the proxy (over TLS) but must NOT appear in the QR.
    assert captured["body"]["easypost_key"] == "EZTKprod_secret"
    payload = json.loads(result["qr_payload"])
    assert payload["t"] == result["pairing_token"]
    assert "EZTKprod_secret" not in result["qr_payload"]
    assert payload["u"].startswith("https://")


def test_missing_key_and_license_raise(monkeypatch):
    monkeypatch.setattr(mp, "load_credentials", lambda: SimpleNamespace(production_key=None))
    monkeypatch.setattr(mp, "load_settings", lambda: SimpleNamespace(license_key="EPD1.x.y"))
    with pytest.raises(PairingError) as e:
        register_pairing()
    assert e.value.reason == "no_production_key"

    monkeypatch.setattr(mp, "load_credentials", lambda: SimpleNamespace(production_key="k"))
    monkeypatch.setattr(mp, "load_settings", lambda: SimpleNamespace(license_key=""))
    with pytest.raises(PairingError) as e:
        register_pairing()
    assert e.value.reason == "no_license"


def test_invalid_license_and_server_and_network(monkeypatch, ready):
    monkeypatch.setattr(mp.requests, "post", lambda *a, **k: _Resp(403))
    with pytest.raises(PairingError) as e:
        register_pairing()
    assert e.value.reason == "invalid_license"

    monkeypatch.setattr(mp.requests, "post", lambda *a, **k: _Resp(500))
    with pytest.raises(PairingError) as e:
        register_pairing()
    assert e.value.reason == "server"

    def boom(*a, **k):
        raise mp.requests.RequestException("down")

    monkeypatch.setattr(mp.requests, "post", boom)
    with pytest.raises(PairingError) as e:
        register_pairing()
    assert e.value.reason == "network"
