"""The screenshot tool must never publish a page that renders a secret.

Store screenshots are public and are fanned out to forty-seven languages, so a
page that puts an API key or a pairing token on screen must not be capturable.
That was enforced for the hard-coded page list but not for `--window`, which
walked straight past the check and could photograph any page in the navigation.

`settings_view` is deliberately absent from the forbidden set: it was listed
because SettingsView.refresh() loaded stored keys into its fields, and 1.2.1
removed that (see test_settings_keys_not_shown.py). These tests pin both halves
of that decision so neither drifts silently.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_make_screenshots():
    """`packaging/` is not a package, so load the module by path."""
    path = REPO_ROOT / "packaging" / "make_screenshots.py"
    spec = importlib.util.spec_from_file_location("make_screenshots", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shots():
    return _load_make_screenshots()


SECRET_BEARING = [
    "setup_wizard",         # first-run form, takes the API key
    "connect_agents_view",  # renders agent pairing tokens
    "pair_mobile_view",     # QR carrying a one-time pairing token
    "license_gate",
    "store_unlock",
]


@pytest.mark.parametrize("module_name", SECRET_BEARING)
def test_secret_bearing_pages_stay_forbidden(shots, module_name):
    assert module_name in shots.FORBIDDEN_PAGES


def test_settings_is_deliberately_capturable(shots):
    """1.2.1 stopped SettingsView rendering keys, so the ban was retired.

    If SettingsView is ever changed to put a key on screen again, this is the
    test that should be flipped back — not quietly worked around.
    """
    assert "settings_view" not in shots.FORBIDDEN_PAGES


def test_every_forbidden_page_names_a_real_module(shots):
    """A name matching no module guards nothing.

    "pair_screen" sat in this set while the real page was pair_mobile_view, so
    the mobile pairing QR — a one-time token — was never actually covered.
    """
    for name in shots.FORBIDDEN_PAGES:
        matches = list(REPO_ROOT.joinpath("app").rglob(f"{name}.py"))
        assert matches, f"{name} in FORBIDDEN_PAGES matches no module under app/"


def test_audit_flags_a_forbidden_capture(shots, tmp_path):
    (tmp_path / "window-ConnectAgentsView.png").write_bytes(b"")
    (tmp_path / "05-setup-wizard.png").write_bytes(b"")
    problems = shots.audit_for_secrets(tmp_path)
    assert len(problems) == 2


def test_audit_allows_a_settings_capture(shots, tmp_path):
    (tmp_path / "en_9_settings.png").write_bytes(b"")
    assert shots.audit_for_secrets(tmp_path) == []


# ── Font coverage and tofu detection ──────────────────────────────────────


def test_locale_writing_system_mapping_covers_all_locales(shots):
    """Every locale JSON must have an entry in the writing system mapping."""
    locales_dir = REPO_ROOT / "app" / "resources" / "locales"
    for f in sorted(locales_dir.glob("*.json")):
        locale = f.stem
        assert locale in shots._LOCALE_WRITING_SYSTEMS, (
            f"Locale '{locale}' has no entry in _LOCALE_WRITING_SYSTEMS"
        )


def test_audit_for_tofu_flags_tiny_images(shots, tmp_path):
    """A PNG with near-zero complexity should be flagged."""
    from PIL import Image

    img = Image.new("RGB", (400, 300), color=(255, 255, 255))
    path = tmp_path / "blank.png"
    img.save(str(path), "PNG")
    problems = shots.audit_for_tofu(tmp_path)
    assert len(problems) == 1
    assert "blank.png" in problems[0]


def test_audit_for_tofu_passes_complex_images(shots, tmp_path):
    """A PNG with real visual content should pass."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 300), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)
    for y in range(0, 300, 20):
        for x in range(0, 400, 15):
            draw.rectangle([x, y, x + 10, y + 12], fill=(50, 50, 50))
    draw.text((20, 20), "Create Shipment", fill=(0, 0, 0))
    draw.text((20, 50), "Royal Mail Click & Drop", fill=(0, 0, 0))
    path = tmp_path / "good.png"
    img.save(str(path), "PNG")
    problems = shots.audit_for_tofu(tmp_path)
    assert problems == []
