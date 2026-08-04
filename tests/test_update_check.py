"""The direct-download "update available" check.

The check compares the running APP_VERSION against the latest GitHub release
tag. It must be fail-silent (any network/parse problem yields "no update"),
version-ordering must be numeric not lexical, and it must no-op on the Store
and Mac App Store builds, which update themselves.
"""

import app.core.update_check as uc


# --- version parsing / ordering -----------------------------------------

def test_parse_version_strips_v_and_orders_numerically():
    assert uc._parse_version("v1.0.8") == (1, 0, 8)
    assert uc._parse_version("1.0.10") == (1, 0, 10)
    # Numeric, not lexical: 1.0.10 must sort above 1.0.9.
    assert uc._parse_version("1.0.10") > uc._parse_version("1.0.9")
    # Malformed parts degrade to 0 rather than raising.
    assert uc._parse_version("1.x.") == (1, 0, 0)


# --- check_for_update ----------------------------------------------------

def test_returns_tag_when_newer(monkeypatch):
    monkeypatch.setattr(uc, "STORE_BUILD", False)
    monkeypatch.setattr(uc, "MAS_BUILD", False)
    monkeypatch.setattr(uc, "APP_VERSION", "1.0.8")
    monkeypatch.setattr(uc, "latest_release_tag", lambda timeout=6.0: "v1.0.9")
    assert uc.check_for_update() == "v1.0.9"


def test_returns_none_when_same_or_older(monkeypatch):
    monkeypatch.setattr(uc, "STORE_BUILD", False)
    monkeypatch.setattr(uc, "MAS_BUILD", False)
    monkeypatch.setattr(uc, "APP_VERSION", "1.0.8")
    monkeypatch.setattr(uc, "latest_release_tag", lambda timeout=6.0: "v1.0.8")
    assert uc.check_for_update() is None
    monkeypatch.setattr(uc, "latest_release_tag", lambda timeout=6.0: "v1.0.7")
    assert uc.check_for_update() is None


def test_fail_silent_when_api_returns_nothing(monkeypatch):
    monkeypatch.setattr(uc, "STORE_BUILD", False)
    monkeypatch.setattr(uc, "MAS_BUILD", False)
    monkeypatch.setattr(uc, "latest_release_tag", lambda timeout=6.0: None)
    assert uc.check_for_update() is None


def test_no_op_on_store_build(monkeypatch):
    """The Store build updates itself; the check must never run there, even if
    a (mocked) newer tag exists."""
    monkeypatch.setattr(uc, "STORE_BUILD", True)
    monkeypatch.setattr(uc, "MAS_BUILD", False)
    monkeypatch.setattr(uc, "APP_VERSION", "1.0.0")
    monkeypatch.setattr(uc, "latest_release_tag", lambda timeout=6.0: "v99.0.0")
    assert uc.update_check_supported() is False
    assert uc.check_for_update() is None


def test_no_op_on_mas_build(monkeypatch):
    monkeypatch.setattr(uc, "STORE_BUILD", False)
    monkeypatch.setattr(uc, "MAS_BUILD", True)
    monkeypatch.setattr(uc, "latest_release_tag", lambda timeout=6.0: "v99.0.0")
    assert uc.update_check_supported() is False
    assert uc.check_for_update() is None
