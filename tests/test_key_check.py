"""Checking an EasyPost key tells the customer what actually went wrong.

Until 1.3.0 every failure — a typo, a key in the wrong field, no connection, an
EasyPost fault — showed one message that blamed the key, and with no
connection the check spun for 84 seconds first. These tests pin each
classification and the time bound.

Network failures are simulated below the real easypost library, at the
requests session it sends through, so the library's own mapping from transport
errors and HTTP statuses to exceptions is exercised as well as ours. Nothing
here touches the network.
"""

import json
import threading
import time
import types

import pytest
import requests

import app.core.easypost_keys as ek
from app.config import MODE_PRODUCTION, MODE_TEST
from app.i18n import LOCALES_DIR, tr
from app.ui.widgets import key_verification as kv


# --- the real library, with the transport replaced ------------------------

def _respond(status: int, body: dict):
    def request(self, *args, **kwargs):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        return response

    return request


def _raise(exc: Exception):
    def request(self, *args, **kwargs):
        raise exc

    return request


def _address_body(mode: str) -> dict:
    return {"id": "adr_x", "object": "Address", "mode": mode, "street1": "417 Montgomery Street"}


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "errors": []}}


@pytest.mark.parametrize("mode", [MODE_TEST, MODE_PRODUCTION])
def test_a_working_key_reports_its_true_mode(monkeypatch, mode):
    monkeypatch.setattr(requests.Session, "request", _respond(201, _address_body(mode)))
    assert ek.check_key("EZTK_anything") == ek.KeyCheck(mode, None)


def test_no_connection_is_a_network_problem_not_a_bad_key(monkeypatch):
    monkeypatch.setattr(
        requests.Session, "request",
        _raise(requests.exceptions.ConnectionError("Failed to resolve 'api.easypost.com'")),
    )
    assert ek.check_key("EZTK_anything") == ek.KeyCheck(None, ek.PROBLEM_NETWORK)


def test_a_request_timeout_is_a_network_problem(monkeypatch):
    monkeypatch.setattr(requests.Session, "request", _raise(requests.exceptions.ReadTimeout("slow")))
    assert ek.check_key("EZTK_anything").problem == ek.PROBLEM_NETWORK


@pytest.mark.parametrize(
    "status, body",
    [
        # What EasyPost actually returns for a made-up key (observed 2026-09-13).
        (403, _error_body("APIKEY.INACTIVE", "This api key is no longer active.")),
        (401, _error_body("APIKEY.REQUIRED", "API key required.")),
    ],
)
def test_a_refused_key_is_an_invalid_key(monkeypatch, status, body):
    monkeypatch.setattr(requests.Session, "request", _respond(status, body))
    assert ek.check_key("not-a-key") == ek.KeyCheck(None, ek.PROBLEM_INVALID_KEY)


@pytest.mark.parametrize("status", [500, 502, 503, 429, 422])
def test_an_easypost_fault_is_not_blamed_on_the_key(monkeypatch, status):
    monkeypatch.setattr(requests.Session, "request", _respond(status, _error_body("X", "boom")))
    assert ek.check_key("EZTK_anything") == ek.KeyCheck(None, ek.PROBLEM_EASYPOST_ERROR)


def test_an_unrecognised_mode_is_never_trusted(monkeypatch):
    monkeypatch.setattr(requests.Session, "request", _respond(201, _address_body("sandbox")))
    assert ek.check_key("EZTK_anything") == ek.KeyCheck(None, ek.PROBLEM_EASYPOST_ERROR)


def test_the_client_is_given_a_short_timeout(monkeypatch):
    seen = {}

    def request(self, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(requests.Session, "request", request)
    ek.check_key("EZTK_anything")
    assert seen["timeout"] == ek.REQUEST_TIMEOUT_S <= 10


# --- the time bound ----------------------------------------------------------

def _hanging_easypost(release: threading.Event):
    """A client whose request never returns until released — like a lookup
    stuck in name resolution, which no socket timeout covers."""
    def create(**_kwargs):
        release.wait(30)
        return types.SimpleNamespace(mode=MODE_TEST)

    def client(_key, **_kwargs):
        return types.SimpleNamespace(address=types.SimpleNamespace(create=create))

    return types.SimpleNamespace(EasyPostClient=client, errors=ek.easypost.errors)


def test_a_hung_check_gives_up_at_the_deadline_and_says_network(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(ek, "easypost", _hanging_easypost(release))
    try:
        started = time.monotonic()
        result = ek.check_key("EZTK_anything", deadline=0.5)
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert result == ek.KeyCheck(None, ek.PROBLEM_NETWORK)
    assert elapsed < 2.0


def test_two_keys_share_one_deadline(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(ek, "easypost", _hanging_easypost(release))
    try:
        started = time.monotonic()
        results = ek.check_keys("EZTK_a", "EZAK_b", deadline=0.6)
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert [r.problem for r in results] == [ek.PROBLEM_NETWORK, ek.PROBLEM_NETWORK]
    assert elapsed < 1.5  # not 2 × deadline


def test_the_default_deadline_is_short():
    assert ek.CHECK_DEADLINE_S <= 15


def test_an_empty_key_is_not_checked(monkeypatch):
    monkeypatch.setattr(requests.Session, "request", _raise(AssertionError("must not be called")))
    assert ek.check_keys("", "  ") == [ek.KeyCheck(None, None), ek.KeyCheck(None, None)]


# --- which field, and which message -----------------------------------------

@pytest.mark.parametrize(
    "key, mode",
    [("EZTKabc", MODE_TEST), ("EZAKabc", MODE_PRODUCTION), ("  EZTKabc ", MODE_TEST), ("abc", None), ("", None)],
)
def test_prefix_claims(key, mode):
    assert ek.mode_from_prefix(key) == mode


def test_a_key_in_the_wrong_field_is_refused_from_its_prefix():
    assert kv.prefix_problem(kv.FIELD_TEST, "EZAKlive") == kv.PROBLEM_WRONG_MODE
    assert kv.prefix_problem(kv.FIELD_PRODUCTION, "EZTKtest") == kv.PROBLEM_WRONG_MODE


def test_a_matching_or_unknown_prefix_is_never_accepted_on_the_prefix_alone():
    # No problem from the prefix just means "ask EasyPost"; it is not a pass.
    assert kv.prefix_problem(kv.FIELD_TEST, "EZTKtest") is None
    assert kv.prefix_problem(kv.FIELD_TEST, "legacy-key-without-prefix") is None
    assert kv.slot_problem(kv.FIELD_TEST, "EZTKtest", ek.KeyCheck(None, ek.PROBLEM_NETWORK)) == ek.PROBLEM_NETWORK


@pytest.mark.parametrize(
    "field, check, problem",
    [
        (kv.FIELD_TEST, ek.KeyCheck(MODE_TEST, None), None),
        (kv.FIELD_PRODUCTION, ek.KeyCheck(MODE_PRODUCTION, None), None),
        (kv.FIELD_TEST, ek.KeyCheck(MODE_PRODUCTION, None), kv.PROBLEM_WRONG_MODE),
        (kv.FIELD_PRODUCTION, ek.KeyCheck(MODE_TEST, None), kv.PROBLEM_WRONG_MODE),
        (kv.FIELD_TEST, ek.KeyCheck(None, ek.PROBLEM_INVALID_KEY), ek.PROBLEM_INVALID_KEY),
        (kv.FIELD_PRODUCTION, ek.KeyCheck(None, ek.PROBLEM_NETWORK), ek.PROBLEM_NETWORK),
        (kv.FIELD_PRODUCTION, ek.KeyCheck(None, ek.PROBLEM_EASYPOST_ERROR), ek.PROBLEM_EASYPOST_ERROR),
    ],
)
def test_slot_problem(field, check, problem):
    assert kv.slot_problem(field, "some-key", check) == problem


def test_every_field_and_problem_has_its_own_message_in_the_catalogue():
    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    problems = [kv.PROBLEM_WRONG_MODE, ek.PROBLEM_INVALID_KEY, ek.PROBLEM_NETWORK, ek.PROBLEM_EASYPOST_ERROR]
    bodies = []
    for field in (kv.FIELD_TEST, kv.FIELD_PRODUCTION):
        for problem in problems:
            title, body = kv.message_for(field, problem)
            assert title in english and body in english
            bodies.append(english[body])
    # Eight distinct explanations: no two failures share a message any more.
    assert len(set(bodies)) == len(bodies)
    # And each names the field it is about.
    for field, word in ((kv.FIELD_TEST, "test"), (kv.FIELD_PRODUCTION, "production")):
        for problem in problems:
            assert word in english[kv.message_for(field, problem)[1]].lower()


# --- the flow the screens use ---------------------------------------------

@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def shown(monkeypatch):
    """Record message boxes instead of opening them."""
    boxes = []
    monkeypatch.setattr(
        kv.QMessageBox, "warning", staticmethod(lambda _w, title, body: boxes.append((title, body)))
    )
    return boxes


def _wait_for(predicate, qt_app, timeout=5.0):
    ends = time.monotonic() + timeout
    while not predicate() and time.monotonic() < ends:
        qt_app.processEvents()
        time.sleep(0.01)
    return predicate()


def test_a_good_test_key_and_a_bad_production_key_names_the_production_field(qt_app, shown, monkeypatch):
    from PySide6.QtWidgets import QWidget

    monkeypatch.setattr(
        kv, "check_keys",
        lambda *_keys: [ek.KeyCheck(MODE_TEST, None), ek.KeyCheck(None, ek.PROBLEM_INVALID_KEY)],
    )
    widget = QWidget()
    fields, ok, busy = [], [], []
    kv.verify_key_slots(
        widget, "EZTKgood", "EZAKbad", on_ok=lambda: ok.append(True),
        on_busy=busy.append, on_field_error=fields.append,
    )
    assert _wait_for(lambda: shown, qt_app)
    assert fields == [kv.FIELD_PRODUCTION]
    assert ok == []
    assert busy == [True, False]
    assert shown == [(tr("key_check.invalid_prod_title"), tr("key_check.invalid_prod_body"))]


def test_a_wrong_field_prefix_is_refused_without_waiting_on_the_network(qt_app, shown, monkeypatch):
    from PySide6.QtWidgets import QWidget

    monkeypatch.setattr(kv, "check_keys", lambda *_k: pytest.fail("network check must not run"))
    fields, busy = [], []
    widget = QWidget()
    kv.verify_key_slots(
        widget, "EZAKlive", "", on_ok=lambda: pytest.fail("must not save"),
        on_busy=busy.append, on_field_error=fields.append,
    )
    assert fields == [kv.FIELD_TEST]
    assert busy == []  # never showed "Checking your key…"
    assert shown == [(tr("key_check.prod_in_test_title"), tr("key_check.prod_in_test_body"))]


def test_offline_setup_is_told_about_the_connection(qt_app, shown, monkeypatch):
    from PySide6.QtWidgets import QWidget

    monkeypatch.setattr(requests.Session, "request", _raise(requests.exceptions.ConnectionError("offline")))
    fields = []
    widget = QWidget()  # held: it owns the check's thread
    kv.verify_key_slots(widget, "EZTKsomething", "", on_ok=lambda: pytest.fail("must not save"),
                        on_field_error=fields.append)
    assert _wait_for(lambda: shown, qt_app)
    assert shown == [(tr("key_check.network_title"), tr("key_check.network_test_body"))]
    assert fields == [kv.FIELD_TEST]


def test_both_keys_good_saves(qt_app, shown, monkeypatch):
    from PySide6.QtWidgets import QWidget

    monkeypatch.setattr(
        kv, "check_keys",
        lambda *_k: [ek.KeyCheck(MODE_TEST, None), ek.KeyCheck(MODE_PRODUCTION, None)],
    )
    ok = []
    widget = QWidget()
    kv.verify_key_slots(widget, "EZTKa", "EZAKb", on_ok=lambda: ok.append(True))
    assert _wait_for(lambda: ok, qt_app)
    assert shown == []
