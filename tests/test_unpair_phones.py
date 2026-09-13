"""Unpairing phones from the desktop: the revoke-all call, the Pair mobile app
page's "Unpair all phones" action, and revoking when Settings replaces or
removes the production key.

The App Store listing says "Unpairing revokes the phone's access". The proxy
can do that since PR #66, but only if the desktop asks it correctly and asks
before it throws away the key the phones were paired with. Every HTTP call here
is mocked; the request shape is checked against the Worker's own parsing code
rather than against what this client happens to send.
"""

import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.config import PAIR_PROXY_URL
from app.core.credential_store import Credentials
from app.i18n import tr
from app.services import mobile_pairing as mp
from app.services.mobile_pairing import PairingError


def revoke_all_phones(*args, **kwargs):
    # Looked up at call time, so a build without it fails each test on its own
    # rather than failing to collect the file.
    return mp.revoke_all_phones(*args, **kwargs)

WORKER_JS = (
    Path(__file__).resolve().parent.parent
    / "server" / "easypost-mobile-proxy" / "src" / "worker.js"
)

# Obviously fake. Nothing in this file may resemble a real EasyPost key.
OLD_KEY = "EZAK_fake_old_production_key"
NEW_KEY = "EZAK_fake_new_production_key"
LICENCE = "EPD1.fake.licence"


class _Resp:
    def __init__(self, status_code, body=None, bad_json=False):
        self.status_code = status_code
        self._body = body
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def stored(monkeypatch):
    """A stored production key and licence; `set` changes either."""
    state = SimpleNamespace(key=OLD_KEY, licence=LICENCE)
    monkeypatch.setattr(mp, "load_credentials", lambda: SimpleNamespace(production_key=state.key))
    monkeypatch.setattr(
        mp, "load_settings",
        lambda: SimpleNamespace(license_key=state.licence, mobile_phones_may_be_paired=None),
    )
    return state


@pytest.fixture
def post(monkeypatch):
    """requests.post, recorded, answering 200 with a count unless told otherwise."""
    fake = MagicMock(return_value=_Resp(200, {"ok": True, "revoked": 2}))
    monkeypatch.setattr(mp.requests, "post", fake)
    return fake


# ---- the Worker's side of the contract -------------------------------------


def _worker_function(name: str) -> str:
    source = WORKER_JS.read_text(encoding="utf-8")
    start = source.index(f"async function {name}(")
    end = source.index("\n}\n", start)
    return source[start:end]


def test_the_worker_routes_revoke_all_as_a_post():
    source = WORKER_JS.read_text(encoding="utf-8")
    assert re.search(
        r'request\.method === "POST" && url\.pathname === "/pair/revoke-all"\s*\)\s*\{\s*'
        r"return handleRevokeAll\(",
        source,
    )


def test_request_matches_what_the_worker_parses(stored, post):
    handler = _worker_function("handleRevokeAll")
    fields = re.search(r"const \{([^}]*)\} = body \|\| \{\};", handler).group(1)
    accepted = {f.strip() for f in fields.split(",") if f.strip()}
    # The only field whose absence the Worker refuses.
    required = set(re.findall(r'if \(!(\w+)\) return json\(\{ error: "missing_fields" \}', handler))
    assert required == {"easypost_key"}
    assert "license" in accepted

    assert revoke_all_phones() == 2

    (url,), kwargs = post.call_args
    assert url == f"{PAIR_PROXY_URL}/pair/revoke-all"
    body = kwargs["json"]
    assert set(body) <= accepted
    assert required <= set(body)
    assert body == {"easypost_key": OLD_KEY, "license": LICENCE}
    assert kwargs["timeout"] > 0


def test_licence_is_left_out_when_there_is_none(stored, post):
    """Store builds hold no licence. The Worker verifies a licence whenever
    `license` is truthy, so an empty string must not be sent as one."""
    handler = _worker_function("handleRevokeAll")
    assert "if (license) {" in handler

    stored.licence = "   "
    revoke_all_phones()
    assert post.call_args.kwargs["json"] == {"easypost_key": OLD_KEY}


def test_an_explicit_key_is_sent_instead_of_the_stored_one(stored, post):
    stored.key = NEW_KEY
    revoke_all_phones(OLD_KEY)
    assert post.call_args.kwargs["json"]["easypost_key"] == OLD_KEY


def test_the_count_is_read_from_the_field_the_worker_returns(stored, post):
    handler = _worker_function("handleRevokeAll")
    assert "return json({ ok: true, revoked });" in handler

    post.return_value = _Resp(200, {"ok": True, "revoked": 0})
    assert revoke_all_phones() == 0


def test_no_key_means_no_request(stored, post):
    stored.key = None
    with pytest.raises(PairingError) as e:
        revoke_all_phones()
    assert e.value.reason == "no_production_key"
    post.assert_not_called()


def test_status_codes_map_to_the_worker_refusals(stored, post):
    handler = _worker_function("handleRevokeAll")
    refusals = dict(re.findall(r'json\(\{ error: "(\w+)" \}, (\d+)\)', handler))
    # 403 is the licence and nothing else, so it can be named to the user.
    assert refusals == {"bad_json": "400", "missing_fields": "400", "invalid_license": "403"}

    for status, reason in ((403, "invalid_license"), (400, "server"), (500, "server"), (404, "server")):
        post.return_value = _Resp(status, {"error": "x"})
        with pytest.raises(PairingError) as e:
            revoke_all_phones()
        assert e.value.reason == reason, status


@pytest.mark.parametrize("response", [
    _Resp(200, bad_json=True),
    _Resp(200, {"ok": True}),
    _Resp(200, {"ok": True, "revoked": "2"}),
    _Resp(200, {"ok": True, "revoked": True}),
    _Resp(200, {"ok": True, "revoked": -1}),
    _Resp(200, ["not", "an", "object"]),
])
def test_a_200_without_a_count_is_not_success(stored, post, response):
    post.return_value = response
    with pytest.raises(PairingError) as e:
        revoke_all_phones()
    assert e.value.reason == "server"


def test_network_failure(stored, post):
    post.side_effect = mp.requests.ConnectionError("down")
    with pytest.raises(PairingError) as e:
        revoke_all_phones()
    assert e.value.reason == "network"


# ---- whether a phone may be paired ----------------------------------------


def test_the_marker_distinguishes_never_recorded_from_none_paired(tmp_path, monkeypatch):
    from app.core import settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(mp, "load_settings", settings_module.load_settings)
    monkeypatch.setattr(mp, "save_settings", settings_module.save_settings)

    # An install that paired before this was tracked: only the proxy knows.
    assert mp.phones_may_be_paired() is True
    mp.record_no_phones_paired()
    assert mp.phones_may_be_paired() is False
    mp.record_pairing_registered()
    assert mp.phones_may_be_paired() is True
    assert settings_module.load_settings().mobile_phones_may_be_paired is True


# ---- UI --------------------------------------------------------------------

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.ui.views import pair_mobile_view as pmv  # noqa: E402
from app.ui.views import settings_view as sv  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, value):
        for slot in self._slots:
            slot(value)


class _Task:
    """Stands in for run_async: records the callable and finishes on demand."""

    def __init__(self, fn):
        self.fn = fn
        self.succeeded = _Signal()
        self.failed = _Signal()

    def finish(self):
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - mirror AsyncTask
            self.failed.emit(exc)
        else:
            self.succeeded.emit(result)


@pytest.fixture
def tasks():
    started = []

    def fake_run_async(fn, parent=None):
        task = _Task(fn)
        started.append(task)
        return task

    return started, fake_run_async


# ---- Pair mobile app page --------------------------------------------------


@pytest.fixture
def page(qt_app, tasks):
    started, fake_run_async = tasks
    with patch.object(pmv, "production_allowed", return_value=True), \
            patch.object(pmv, "production_key", return_value=OLD_KEY), \
            patch.object(pmv, "run_async", side_effect=fake_run_async), \
            patch.object(pmv, "record_no_phones_paired") as record_none, \
            patch.object(pmv, "record_pairing_registered") as record_paired, \
            patch.object(pmv, "revoke_all_phones", return_value=3) as revoke, \
            patch.object(pmv.QMessageBox, "question") as question, \
            patch.object(pmv.QMessageBox, "information") as information, \
            patch.object(pmv.QMessageBox, "warning") as warning:
        view = pmv.PairMobileView()
        yield SimpleNamespace(
            view=view, started=started, revoke=revoke, question=question,
            information=information, warning=warning,
            record_none=record_none, record_paired=record_paired,
        )


def test_unpair_asks_first_and_says_phones_must_pair_again(page):
    page.question.return_value = QMessageBox.StandardButton.No
    page.view._unpair_btn.click()

    (_parent, _title, body), _ = page.question.call_args
    assert body == tr("pair_mobile.unpair_all_confirm_body")
    assert "every phone" in body and "pair again" in body
    # Declining sends nothing.
    assert page.started == []
    page.revoke.assert_not_called()


def test_unpair_runs_in_the_background_and_reports_the_count(page):
    page.question.return_value = QMessageBox.StandardButton.Yes
    page.view._qr_group.setVisible(True)
    page.view._unpair_btn.click()

    assert len(page.started) == 1
    # Nothing has been sent on the UI thread; the call is the task's to make.
    page.revoke.assert_not_called()
    assert not page.view._unpair_btn.isEnabled()
    assert page.view._unpair_btn.text() == tr("pair_mobile.unpairing_button")

    page.started[0].finish()

    page.revoke.assert_called_once_with()
    (_parent, _title, body), _ = page.information.call_args
    assert body == tr("pair_mobile.unpair_all_done", count=3)
    assert "3" in body
    page.record_none.assert_called_once()
    # revoke-all deletes the waiting pairing too, so its QR must not stay up.
    assert not page.view._qr_group.isVisibleTo(page.view)
    assert page.view._unpair_btn.isEnabled()
    assert page.view._unpair_btn.text() == tr("pair_mobile.unpair_all_button")


def test_unpair_with_nothing_to_revoke_says_so(page):
    page.question.return_value = QMessageBox.StandardButton.Yes
    page.revoke.return_value = 0
    page.view._unpair_btn.click()
    page.started[0].finish()

    (_parent, _title, body), _ = page.information.call_args
    assert body == tr("pair_mobile.unpair_all_none")


@pytest.mark.parametrize("exc, reason", [
    (PairingError("network"), "network"),
    (PairingError("invalid_license"), "invalid_license"),
    (PairingError("server"), "server"),
    (PairingError("something_new"), "server"),
    (RuntimeError("boom"), "server"),
])
def test_unpair_failures_show_a_translated_reason(page, exc, reason):
    page.question.return_value = QMessageBox.StandardButton.Yes
    page.revoke.side_effect = exc
    page.view._unpair_btn.click()
    page.started[0].finish()

    (_parent, _title, body), _ = page.warning.call_args
    assert body == tr(f"pair_mobile.error_{reason}")
    page.information.assert_not_called()
    page.record_none.assert_not_called()
    assert page.view._unpair_btn.isEnabled()


def test_unpair_needs_a_key_but_not_a_licence(qt_app):
    with patch.object(pmv, "production_allowed", return_value=False), \
            patch.object(pmv, "production_key", return_value=OLD_KEY):
        view = pmv.PairMobileView()
    # A lapsed licence must not stop someone cutting off a lost phone.
    assert not view._generate_btn.isEnabled()
    assert view._unpair_btn.isEnabled()

    with patch.object(pmv, "production_allowed", return_value=True), \
            patch.object(pmv, "production_key", return_value=None):
        view = pmv.PairMobileView()
    assert not view._unpair_btn.isEnabled()


def test_generating_a_code_records_that_a_phone_may_be_paired(page):
    page.view._on_registered({"qr_payload": '{"t":"x","u":"https://example.invalid"}'})
    page.record_paired.assert_called_once()


def test_unpair_really_leaves_the_ui_thread(qt_app):
    """The fake above proves the view hands the call to run_async; this proves
    run_async is what keeps it off the thread that paints the window."""
    seen = {}
    done = threading.Event()

    def fake_revoke():
        seen["thread"] = threading.get_ident()
        return 1

    with patch.object(pmv, "production_allowed", return_value=True), \
            patch.object(pmv, "production_key", return_value=OLD_KEY), \
            patch.object(pmv, "revoke_all_phones", side_effect=fake_revoke), \
            patch.object(pmv, "record_no_phones_paired"), \
            patch.object(pmv.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
            patch.object(pmv.QMessageBox, "information", side_effect=lambda *a: done.set()):
        view = pmv.PairMobileView()
        view._unpair_btn.click()
        for _ in range(500):
            if done.is_set():
                break
            qt_app.processEvents()
            threading.Event().wait(0.01)

    assert done.is_set(), "the revoke never reported back"
    assert seen["thread"] != threading.get_ident()


# ---- Settings: replacing or removing the production key -------------------


@pytest.fixture
def settings_page(qt_app, tasks):
    started, fake_run_async = tasks
    creds = Credentials(test_key="EZTK_fake_test", production_key=OLD_KEY, active_mode="test")
    saved = []

    def save(c):
        saved.append((c.test_key, c.production_key))

    with patch.object(sv, "load_credentials", return_value=creds), \
            patch.object(sv, "save_credentials", side_effect=save), \
            patch.object(sv, "verify_key_slots",
                         side_effect=lambda w, t, p, on_ok, on_busy=None: on_ok()), \
            patch.object(sv, "run_async", side_effect=fake_run_async), \
            patch.object(sv, "phones_may_be_paired", return_value=True) as may_be_paired, \
            patch.object(sv, "record_no_phones_paired") as record_none, \
            patch.object(sv, "revoke_all_phones", return_value=2) as revoke, \
            patch.object(sv.QMessageBox, "question") as question, \
            patch.object(sv.QMessageBox, "information") as information:
        view = sv.SettingsView()
        yield SimpleNamespace(
            view=view, creds=creds, saved=saved, started=started, revoke=revoke,
            may_be_paired=may_be_paired, record_none=record_none,
            question=question, information=information,
        )


def test_replacing_the_key_revokes_with_the_old_key_before_saving(settings_page):
    s = settings_page
    s.view._prod_key_input.setText(NEW_KEY)
    s.view._on_save()

    # Nothing is saved while the revoke is outstanding: the old key must still
    # be here if it fails and the user declines.
    assert len(s.started) == 1
    assert s.saved == []
    assert not s.view._save_btn.isEnabled()
    assert not s.view._forget_btn.isEnabled()

    s.started[0].finish()

    s.revoke.assert_called_once_with(OLD_KEY)
    assert s.saved == [("EZTK_fake_test", NEW_KEY)]
    s.record_none.assert_called_once()
    (_parent, _title, body), _ = s.information.call_args
    assert tr("settings.saved_body") in body
    assert tr("settings.phones_unpaired_body", count=2) in body
    assert s.view._save_btn.isEnabled() and s.view._forget_btn.isEnabled()


def test_nothing_revoked_on_a_key_change_is_not_announced(settings_page):
    s = settings_page
    s.revoke.return_value = 0
    s.view._prod_key_input.setText(NEW_KEY)
    s.view._on_save()
    s.started[0].finish()

    (_parent, _title, body), _ = s.information.call_args
    assert body == tr("settings.saved_body")


def test_no_call_when_this_computer_never_paired_under_the_key(settings_page):
    s = settings_page
    s.may_be_paired.return_value = False
    s.view._prod_key_input.setText(NEW_KEY)
    s.view._on_save()

    assert s.started == []
    s.revoke.assert_not_called()
    assert s.saved == [("EZTK_fake_test", NEW_KEY)]


@pytest.mark.parametrize("typed_test, typed_prod, old_prod", [
    ("", "", OLD_KEY),               # Save with nothing typed
    ("EZTK_fake_other", "", OLD_KEY),  # only the test key changes
    ("", OLD_KEY, OLD_KEY),          # the same production key again
    ("", NEW_KEY, None),             # the first production key ever
])
def test_no_call_when_no_production_key_is_being_replaced(settings_page, typed_test, typed_prod, old_prod):
    s = settings_page
    s.creds.production_key = old_prod
    s.view._test_key_input.setText(typed_test)
    s.view._prod_key_input.setText(typed_prod)
    s.view._on_save()

    assert s.started == []
    s.revoke.assert_not_called()
    assert len(s.saved) == 1


def test_a_first_production_key_records_that_nothing_is_paired(settings_page):
    s = settings_page
    s.creds.production_key = None
    s.view._prod_key_input.setText(NEW_KEY)
    s.view._on_save()
    s.record_none.assert_called_once()


@pytest.mark.parametrize("answer, saves", [
    (QMessageBox.StandardButton.No, False),
    (QMessageBox.StandardButton.Yes, True),
])
def test_a_failed_revoke_on_key_change_lets_the_user_decide(settings_page, answer, saves):
    s = settings_page
    s.revoke.side_effect = PairingError("network")
    s.question.return_value = answer
    s.view._prod_key_input.setText(NEW_KEY)
    s.view._on_save()
    s.started[0].finish()

    (_parent, title, body), _ = s.question.call_args
    assert title == tr("settings.unpair_failed_title")
    assert body == tr("settings.unpair_failed_save_body", error=tr("pair_mobile.error_network"))
    assert s.saved == ([("EZTK_fake_test", NEW_KEY)] if saves else [])
    assert s.view._save_btn.isEnabled()


def test_forgetting_the_keys_revokes_with_the_old_key_first(settings_page):
    s = settings_page
    s.question.return_value = QMessageBox.StandardButton.Yes
    s.view._on_forget_keys()

    assert len(s.started) == 1
    assert s.saved == []
    assert s.creds.production_key == OLD_KEY

    s.started[0].finish()

    s.revoke.assert_called_once_with(OLD_KEY)
    assert s.saved == [(None, None)]
    (_parent, _title, body), _ = s.information.call_args
    assert body == tr("settings.phones_unpaired_body", count=2)


def test_forgetting_the_keys_without_pairings_makes_no_call(settings_page):
    s = settings_page
    s.may_be_paired.return_value = False
    s.question.return_value = QMessageBox.StandardButton.Yes
    s.view._on_forget_keys()

    assert s.started == []
    assert s.saved == [(None, None)]
    s.information.assert_not_called()


def test_a_failed_revoke_on_forget_keeps_the_keys_if_declined(settings_page):
    s = settings_page
    s.revoke.side_effect = PairingError("invalid_license")
    # First question confirms forgetting; the second declines going ahead.
    s.question.side_effect = [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No]
    s.view._on_forget_keys()
    s.started[0].finish()

    (_parent, _title, body), _ = s.question.call_args
    assert body == tr(
        "settings.unpair_failed_forget_body", error=tr("pair_mobile.error_invalid_license")
    )
    assert s.saved == []
    assert s.creds.production_key == OLD_KEY
