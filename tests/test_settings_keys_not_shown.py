"""The Settings page must never display a stored API key.

Until 1.2.1 it did: `refresh()` loaded both keys out of the credential store and
wrote them into the input boxes, and the Show keys toggle then revealed them in
full. A live production key was therefore readable by anyone looking at the
screen, and captured by any screenshot, screen share or recording. The
screenshot generator had to keep `settings_view` on a forbidden list because of
it — which was the signal that the page, not the screenshot, was the problem.

These tests pin the behaviour that replaced it:

* the fields start empty even when both keys are stored;
* a blank field on save leaves the stored key alone, so opening Settings and
  pressing Save cannot wipe credentials;
* a typed value does replace the stored key.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.views import settings_view as sv  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _stored(test_key="EZTK_stored_test", production_key="EZAK_stored_prod"):
    return SimpleNamespace(
        test_key=test_key,
        production_key=production_key,
        active_mode="test",
        has_mode=lambda mode: True,
    )


def _view(qt_app, creds):
    with patch.object(sv, "load_credentials", return_value=creds):
        return sv.SettingsView()


def test_stored_keys_are_never_written_into_the_fields(qt_app):
    creds = _stored()
    view = _view(qt_app, creds)

    assert view._test_key_input.text() == ""
    assert view._prod_key_input.text() == ""


def test_a_stored_key_is_indicated_without_revealing_it(qt_app):
    creds = _stored()
    view = _view(qt_app, creds)

    for field in (view._test_key_input, view._prod_key_input):
        placeholder = field.placeholderText()
        assert placeholder, "a stored key should be signalled somehow"
        # The mask must not be, or contain, the key itself.
        assert "EZTK" not in placeholder and "EZAK" not in placeholder
        assert creds.test_key not in placeholder
        assert creds.production_key not in placeholder


def test_no_placeholder_when_nothing_is_stored(qt_app):
    view = _view(qt_app, _stored(test_key=None, production_key=None))

    assert view._test_key_input.placeholderText() == ""
    assert view._prod_key_input.placeholderText() == ""


def test_saving_with_blank_fields_keeps_the_stored_keys(qt_app):
    """The dangerous case: the fields are empty by design, so treating blank as
    'clear' would delete both keys the first time anyone pressed Save."""
    creds = _stored()
    view = _view(qt_app, creds)

    with patch.object(sv, "load_credentials", return_value=creds), \
            patch.object(sv, "save_credentials"), \
            patch.object(sv, "verify_key_slots",
                         side_effect=lambda w, t, p, on_ok, on_busy=None: on_ok()), \
            patch.object(sv.QMessageBox, "information"):
        view._on_save()

    assert creds.test_key == "EZTK_stored_test"
    assert creds.production_key == "EZAK_stored_prod"


def test_a_typed_key_replaces_the_stored_one(qt_app):
    creds = _stored()
    view = _view(qt_app, creds)
    view._test_key_input.setText("EZTK_freshly_typed")

    with patch.object(sv, "load_credentials", return_value=creds), \
            patch.object(sv, "save_credentials"), \
            patch.object(sv, "verify_key_slots",
                         side_effect=lambda w, t, p, on_ok, on_busy=None: on_ok()), \
            patch.object(sv.QMessageBox, "information"):
        view._on_save()

    assert creds.test_key == "EZTK_freshly_typed"
    # The untouched field left its key alone.
    assert creds.production_key == "EZAK_stored_prod"
