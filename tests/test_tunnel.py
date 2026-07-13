import pytest

from app.core.tunnel import CloudflareTunnel, CloudflaredNotInstalledError, _URL_PATTERN, install_instructions


def test_url_pattern_matches_sample_cloudflared_output():
    line = "2026-07-13T12:00:00Z INF |  https://abc-def-123.trycloudflare.com  |"
    match = _URL_PATTERN.search(line)
    assert match is not None
    assert match.group(0) == "https://abc-def-123.trycloudflare.com"


def test_install_instructions_nonempty():
    assert install_instructions()


def test_start_raises_when_cloudflared_missing(monkeypatch):
    monkeypatch.setattr("app.core.tunnel.shutil.which", lambda _name: None)
    tunnel = CloudflareTunnel()
    with pytest.raises(CloudflaredNotInstalledError):
        tunnel.start(local_port=12345)
