"""The first-run setup and unlock screens, on a real QApplication.

Pins the onboarding fixes from the 1.3.0 first-ten-minutes audit:

* a language chosen on the setup screen applies to the whole app at once, not
  just to the setup screen until a restart;
* setup finishing goes through the same routing as a launch, so a
  production-only setup on a licensed build meets the licence gate;
* choosing Production with no production key says so instead of snapping back;
* each store build's unlock screen names only its own store;
* the licence gate does not scold an empty field.
"""

import ast
import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.core import credential_store  # noqa: E402
from app.core.db import init_db  # noqa: E402
from app.core.settings import load_settings, save_settings  # noqa: E402
from app.i18n import LOCALES_DIR, SUPPORTED_LOCALES, tr  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fresh_install(qt_app, session_keyring, monkeypatch):
    """No stored keys, English, an unflagged build; all restored afterwards."""
    import app.ui.main_window as mw

    saved_entries = dict(session_keyring.entries)
    session_keyring.entries.clear()
    settings = load_settings()
    saved_locale = settings.locale
    settings.locale = "en"
    save_settings(settings)
    init_db()
    for flag in ("LICENSE_REQUIRED", "STORE_BUILD", "MAS_BUILD"):
        monkeypatch.setattr(mw, flag, False)
    # Nothing on these screens may open a real dialog or browser in a test.
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(mw.MainWindow, "_maybe_check_update", lambda self: None)
    yield mw
    session_keyring.entries.clear()
    session_keyring.entries.update(saved_entries)
    settings = load_settings()
    settings.locale = saved_locale
    save_settings(settings)
    from app.core.client import client_manager

    client_manager.reload()


def _catalogue(code: str) -> dict:
    return json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))


def _finish_setup(window, monkeypatch, test_key="EZTK_onboarding_test", prod_key=""):
    """Press Save and continue with the EasyPost check answered 'fine'."""
    import app.ui.views.setup_wizard as sw

    monkeypatch.setattr(
        sw, "verify_key_slots", lambda _w, _t, _p, on_ok, **_callbacks: on_ok()
    )
    wizard = window._setup_wizard
    wizard._test_key_input.setText(test_key)
    wizard._prod_key_input.setText(prod_key)
    wizard._continue_btn.click()


def _nav_labels(window) -> list[str]:
    return [window._nav.item(row).text() for row in range(window._nav.count())]


# --- language ---------------------------------------------------------------

def test_a_language_chosen_during_setup_applies_to_the_whole_app(fresh_install, monkeypatch):
    window = fresh_install.MainWindow()
    try:
        assert window._root_stack.currentWidget() is window._setup_wizard
        # Nothing has been built in English behind the wizard's back.
        assert window._app_shell is None

        combo = window._setup_wizard._language_combo
        combo.setCurrentIndex(combo.findData("de"))
        _finish_setup(window, monkeypatch)

        german = _catalogue("de")
        assert window._root_stack.currentWidget() is window._app_shell
        labels = _nav_labels(window)
        assert german["main_window.nav_dashboard"] in labels
        assert german["main_window.nav_settings"] in labels
        assert _catalogue("en")["main_window.nav_dashboard"] not in labels
        assert window._mode_banner._label.text() == german["mode_banner.test_label"]
        assert window._mode_banner._selector.itemText(0) == german["mode_banner.test_mode_item"]
    finally:
        window.close()
        window.deleteLater()


def test_a_shell_built_before_a_language_change_is_rebuilt_when_shown_again(fresh_install, monkeypatch):
    credential_store.save_credentials(
        credential_store.Credentials(test_key="EZTK_onboarding_test", active_mode="test")
    )
    window = fresh_install.MainWindow()
    try:
        assert window._root_stack.currentWidget() is window._app_shell
        english_shell = window._app_shell
        assert _catalogue("en")["main_window.nav_dashboard"] in _nav_labels(window)

        settings = load_settings()
        settings.locale = "de"
        save_settings(settings)
        window._show_app_shell()

        assert window._app_shell is not english_shell
        assert window._root_stack.currentWidget() is window._app_shell
        assert _catalogue("de")["main_window.nav_dashboard"] in _nav_labels(window)
        # Showing it again in the same language reuses it.
        shell = window._app_shell
        window._show_app_shell()
        assert window._app_shell is shell
    finally:
        window.close()
        window.deleteLater()


# --- routing after setup ------------------------------------------------------

def test_a_production_only_setup_on_a_licensed_build_meets_the_licence_gate(fresh_install, monkeypatch):
    mw = fresh_install
    window = mw.MainWindow()
    try:
        monkeypatch.setattr(mw, "LICENSE_REQUIRED", True)
        monkeypatch.setattr(mw, "load_active_license", lambda: None)
        _finish_setup(window, monkeypatch, test_key="", prod_key="EZAK_onboarding_prod")
        assert window._root_stack.currentWidget() is window._license_gate
    finally:
        window.close()
        window.deleteLater()


def test_choosing_production_without_a_production_key_opens_settings_at_that_field(fresh_install, monkeypatch):
    import app.ui.widgets.mode_banner as mb

    credential_store.save_credentials(
        credential_store.Credentials(test_key="EZTK_onboarding_test", active_mode="test")
    )
    monkeypatch.setattr(mb, "production_allowed", lambda: True)
    window = fresh_install.MainWindow()
    try:
        window.show()
        selector = window._mode_banner._selector
        selector.setCurrentIndex(selector.findData("production"))
        QApplication.processEvents()

        assert selector.currentData() == "test"  # still no key, so still test
        current = window._view_stack.currentWidget()
        assert current.widget() is window._settings_view
        assert window._settings_view._prod_key_hint.isVisibleTo(window._settings_view)
        assert window._settings_view._prod_key_hint.text() == tr("settings.add_production_key_hint")
    finally:
        window.close()
        window.deleteLater()


# --- setup screen copy ----------------------------------------------------------

def test_setup_shows_real_key_prefixes_and_a_route_to_an_account(qt_app):
    from app.core.easypost_keys import PRODUCTION_KEY_PREFIX, TEST_KEY_PREFIX
    from app.ui.views.setup_wizard import (
        EASYPOST_API_KEYS_URL,
        EASYPOST_SIGNUP_URL,
        SetupWizard,
    )

    wizard = SetupWizard()
    assert wizard._test_key_input.placeholderText().startswith(TEST_KEY_PREFIX)
    assert wizard._prod_key_input.placeholderText().startswith(PRODUCTION_KEY_PREFIX)
    help_text = wizard._account_help_label.text()
    assert f'href="{EASYPOST_SIGNUP_URL}"' in help_text
    assert f'href="{EASYPOST_API_KEYS_URL}"' in help_text


def test_mas_setup_shows_plain_text_urls_not_clickable_links(qt_app, monkeypatch):
    """On MAS builds, the setup wizard must not open a browser (Guideline 4).
    The EasyPost URLs are displayed as selectable plain text instead."""
    import app.ui.views.setup_wizard as sw
    from app.ui.views.setup_wizard import (
        EASYPOST_API_KEYS_URL,
        EASYPOST_SIGNUP_URL,
        SetupWizard,
    )

    monkeypatch.setattr(sw, "MAS_BUILD", True)
    wizard = SetupWizard()
    help_text = wizard._account_help_label.text()
    # Must contain the bare URLs (not wrapped in <a> tags)
    assert EASYPOST_SIGNUP_URL in help_text
    assert EASYPOST_API_KEYS_URL in help_text
    # Must NOT contain HTML link markup
    assert "<a " not in help_text
    assert "href=" not in help_text
    # Must be plain-text format, not rich text
    from PySide6.QtCore import Qt

    assert wizard._account_help_label.textFormat() == Qt.TextFormat.PlainText


def test_every_catalogue_keeps_both_account_links_and_the_prefixes():
    for code, _english, _native in SUPPORTED_LOCALES:
        catalogue = _catalogue(code)
        help_text = catalogue["setup_wizard.account_help"]
        assert help_text.count('<a href="{signup_url}">') == 1, code
        assert help_text.count('<a href="{keys_url}">') == 1, code
        assert help_text.count("</a>") == 2, code
        assert catalogue["setup_wizard.test_key_placeholder"].startswith("EZTK"), code
        assert catalogue["setup_wizard.prod_key_placeholder"].startswith("EZAK"), code


# --- store builds name their own store ----------------------------------------

# Words that identify the other store, by the build that must not show them.
_OTHER_STORE_WORDS = {
    "mas": ("Microsoft", "Windows"),
    "store": ("App Store", "Apple", "macOS", "Mac "),
}


def _gate_keys_for(mas_build: bool) -> set[str]:
    """Every store_unlock key the gate can show on that build, resolved."""
    from app.ui.views import store_unlock

    source = (REPO_ROOT / "app" / "ui" / "views" / "store_unlock.py").read_text(encoding="utf-8")
    keys = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("store_unlock.") and not node.value.endswith("_mac"):
                keys.add(node.value)
    resolved = {store_unlock.store_key(k, mas_build=mas_build) for k in keys}
    # The link sentence is only on the Store build, the plain one only on MAS.
    if mas_build:
        resolved.discard("store_unlock.multi_seat_link")
        resolved.add("store_unlock.multi_seat_text_mac")
    return resolved


def test_store_named_messages_are_never_shown_through_plain_tr():
    """A store-named key passed straight to tr() would show the Windows
    wording on the Mac build — the defect this replaced."""
    from app.ui.views import store_unlock

    source = (REPO_ROOT / "app" / "ui" / "views" / "store_unlock.py").read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "tr"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in store_unlock.STORE_NAMED_KEYS
        ):
            offenders.append(node.args[0].value)
    assert offenders == []


@pytest.mark.parametrize("build", ["mas", "store"])
def test_each_store_build_names_only_its_own_store_in_every_language(build):
    keys = _gate_keys_for(mas_build=build == "mas")
    english = _catalogue("en")
    assert keys <= set(english)
    problems = []
    for code, _english, _native in SUPPORTED_LOCALES:
        catalogue = _catalogue(code)
        for key in keys:
            for word in _OTHER_STORE_WORDS[build]:
                if word in catalogue.get(key, ""):
                    problems.append((code, key, word))
    assert problems == []


def test_the_english_store_messages_do_name_the_right_store():
    """Positive control for the test above: the same words are found where
    they belong, so the check is not passing because it can see nothing."""
    english = _catalogue("en")
    assert "App Store" in english["store_unlock.buy_in_store_title_mac"]
    assert "Apple ID" in english["store_unlock.restore_none_body_mac"]
    assert "Microsoft" in english["store_unlock.buy_in_store_title"]
    assert "Microsoft" in english["store_unlock.restore_none_body"]


def _gate(qt_app, monkeypatch, mas_build: bool):
    from app.ui.views import store_unlock

    monkeypatch.setattr(store_unlock, "MAS_BUILD", mas_build)
    shown = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            store_unlock.QMessageBox, name,
            staticmethod(lambda _w, title, body: shown.append(title + " " + body)),
        )
    opened = []
    monkeypatch.setattr(store_unlock.QDesktopServices, "openUrl", staticmethod(opened.append))
    gate = store_unlock.StoreUnlockGate()
    return gate, shown, opened


@pytest.mark.parametrize("mas_build", [True, False])
def test_the_unlock_gate_dialogs_name_the_right_store(qt_app, monkeypatch, mas_build):
    gate, shown, _opened = _gate(qt_app, monkeypatch, mas_build)
    result = gate._ent.PurchaseResult
    gate._on_purchase_done(result.UNAVAILABLE)
    gate._on_purchase_done(result.ERROR)
    gate._on_purchase_failed(RuntimeError("x"))
    gate._on_restore_done(False)
    assert len(shown) == 4
    text = " ".join(shown)
    forbidden = _OTHER_STORE_WORDS["mas" if mas_build else "store"]
    assert not any(word in text for word in forbidden), text
    assert ("App Store" if mas_build else "Microsoft Store") in text


def test_the_mac_gate_describes_multi_seat_licensing_without_linking_out(qt_app, monkeypatch):
    from PySide6.QtCore import Qt

    gate, _shown, opened = _gate(qt_app, monkeypatch, mas_build=True)
    label = gate._multi_seat_label
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert "<a" not in label.text()
    gate._on_multi_seat()
    assert opened == []


def test_the_mac_gate_hides_the_enter_code_button(qt_app, monkeypatch):
    """Guideline 2.4.5(vi) and 3.1.1 prohibit licence-key entry in MAS apps.
    The button must be hidden on MAS builds but visible on Store builds."""
    gate_mas, _s1, _o1 = _gate(qt_app, monkeypatch, mas_build=True)
    assert gate_mas._code_btn.isHidden()

    gate_store, _s2, _o2 = _gate(qt_app, monkeypatch, mas_build=False)
    assert not gate_store._code_btn.isHidden()


def test_the_unlock_gate_shows_the_store_price_only_when_the_store_gives_one(qt_app, monkeypatch):
    gate, _shown, _opened = _gate(qt_app, monkeypatch, mas_build=False)
    assert gate._price_label.isHidden()
    gate._on_price("£79.00")
    assert not gate._price_label.isHidden()
    assert gate._price_label.text() == tr("store_unlock.price_line", price="£79.00")
    gate._on_price(None)
    assert gate._price_label.isHidden()


def test_the_windows_store_price_is_read_from_the_store_listing(monkeypatch):
    import types

    import app.core.store_entitlement as se

    product = types.SimpleNamespace(price=types.SimpleNamespace(formatted_price="79,00 €"))

    class Products:
        def lookup(self, store_id):
            assert store_id == se.STORE_ADDON_STORE_ID
            return product

    class Context:
        def get_store_products_async(self, kinds, ids):
            assert list(ids) == [se.STORE_ADDON_STORE_ID]
            return types.SimpleNamespace(products=Products())

    monkeypatch.setattr(se, "_store_context", lambda: Context())
    monkeypatch.setattr(se, "_await", lambda op: op)
    assert se.unlock_price() == "79,00 €"


def test_no_price_is_invented_off_the_store():
    import app.core.mac_store_entitlement as mse
    import app.core.store_entitlement as se

    # Tests run unpackaged and without the store flags, so neither store answers.
    assert se.unlock_price() is None
    assert mse.unlock_price() is None


# --- licence gate -----------------------------------------------------------------

def test_the_licence_gate_waits_for_a_key_before_offering_activate(qt_app, monkeypatch):
    from app.ui.views import license_gate

    warned = []
    monkeypatch.setattr(license_gate.QMessageBox, "warning", staticmethod(lambda *a: warned.append(a)))
    gate = license_gate.LicenseGate()
    assert not gate._activate_btn.isEnabled()
    gate._on_activate()  # Enter pressed on the empty field
    assert warned == []
    gate._key_input.setText("  EPD1-something")
    assert gate._activate_btn.isEnabled()
    gate._key_input.setText("   ")
    assert not gate._activate_btn.isEnabled()
    assert gate._title_label.text().startswith("<h2>")


def test_the_licence_gate_spells_licence_one_way():
    english = _catalogue("en")
    gate_text = " ".join(v for k, v in english.items() if k.startswith("license_gate."))
    assert "license" not in gate_text.lower()


# --- startup hooks from other features, under lazy shell construction -------

def test_the_startup_purchase_check_can_open_connect_ai_agents_on_a_lazily_built_shell(
    fresh_install, monkeypatch
):
    """The unknown-outcome check (#72) runs from _show_app_shell and may open
    Connect AI Agents. The shell no longer exists at construction, so the view
    it opens must be the one in the shell actually on screen — including after
    a language change has replaced that shell."""
    import types

    mw = fresh_install
    import app.services.mcp_runner as runner

    monkeypatch.setattr(mw, "MCP_SUPPORTED", True)
    monkeypatch.setattr(mw.MainWindow, "_maybe_resume_relay", lambda self: None)
    report = types.SimpleNamespace(unresolved=[object()])
    monkeypatch.setattr(runner, "reconcile_unknown_outcomes", lambda: report)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    credential_store.save_credentials(
        credential_store.Credentials(test_key="EZTK_onboarding_test", active_mode="test")
    )

    def settle(window):
        task = window._reconcile_task
        for _ in range(200):
            QApplication.processEvents()
            if not task._thread.isRunning() and window._view_stack.currentWidget().widget() is window._connect_agents_view:
                return True
            task._thread.wait(10)
        return False

    window = mw.MainWindow()
    try:
        assert window._root_stack.currentWidget() is window._app_shell
        assert settle(window)
        assert window._retired_shells == []

        settings = load_settings()
        settings.locale = "de"
        save_settings(settings)
        window._show_view(window._dashboard_view)
        window._show_app_shell()  # rebuilt in German; the check runs again
        assert settle(window)
        # The page opened is the new shell's, not the retired English one.
        assert window._app_shell.isAncestorOf(window._connect_agents_view)
        assert all(not old.isAncestorOf(window._connect_agents_view) for old in window._retired_shells)
    finally:
        window.close()
        window.deleteLater()


def test_saving_keys_in_settings_on_a_lazily_built_shell_still_reaches_the_phone_revocation(
    fresh_install, monkeypatch
):
    """Replacing the production key revokes paired phones (#71). The Settings
    page is now built with the shell rather than with the window; its key save
    must still go through verification and that revocation path."""
    mw = fresh_install
    import app.ui.views.settings_view as sv

    credential_store.save_credentials(
        credential_store.Credentials(
            test_key="EZTK_onboarding_test", production_key="EZAK_old", active_mode="test"
        )
    )
    window = mw.MainWindow()
    try:
        view = window._settings_view
        assert window._app_shell.isAncestorOf(view)
        seen = {}
        monkeypatch.setattr(
            sv, "verify_key_slots",
            lambda _w, t, p, on_ok, **callbacks: (seen.update(callbacks=set(callbacks)), on_ok()),
        )
        monkeypatch.setattr(sv, "phones_may_be_paired", lambda: True)
        revoked = []
        monkeypatch.setattr(
            view, "_revoke_phones_then",
            lambda old, commit, _body: (revoked.append(old), commit(1)),
        )
        view._prod_key_input.setText("EZAK_new")
        view._on_save()
        assert revoked == ["EZAK_old"]
        assert "on_field_error" in seen["callbacks"]
        assert credential_store.load_credentials().production_key == "EZAK_new"
    finally:
        window.close()
        window.deleteLater()
