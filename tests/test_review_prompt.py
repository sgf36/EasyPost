"""In-app review prompt — the gates.

The platform calls themselves (SKStoreReviewController, RequestRateAndReviewAppAsync)
can only run inside a real store-installed app and report nothing useful even
then, so nothing here tries to test Apple's or Microsoft's behaviour. What is
tested is the *decision*: every gate blocks on its own, and a prompt is requested
exactly once when all of them pass.
"""

from datetime import timedelta

import pytest

import app.core.review_prompt as rp
from app.core.settings import AppSettings


@pytest.fixture
def mem_settings(monkeypatch):
    """In-memory settings so counters never touch disk."""
    state = {"s": AppSettings()}
    monkeypatch.setattr(rp, "load_settings", lambda: state["s"])
    monkeypatch.setattr(rp, "save_settings", lambda s: state.__setitem__("s", s))
    return state


@pytest.fixture
def store_build(monkeypatch):
    monkeypatch.setattr(rp, "STORE_BUILD", True)
    monkeypatch.setattr(rp, "MAS_BUILD", False)


@pytest.fixture
def in_production(monkeypatch):
    """Production mode, licensed — the state in which prompting is allowed."""
    import app.core.client as client_mod
    import app.core.license as license_mod

    monkeypatch.setattr(client_mod.client_manager, "is_production", lambda: True)
    monkeypatch.setattr(license_mod, "production_allowed", lambda: True)


@pytest.fixture(autouse=True)
def clean_session():
    rp.reset_session_friction()
    yield
    rp.reset_session_friction()


@pytest.fixture
def eligible(mem_settings, store_build, in_production):
    """Every gate satisfied, so each test can break exactly one."""
    s = mem_settings["s"]
    s.review_success_count = rp.SUCCESSES_BEFORE_PROMPT
    s.review_prompt_count = 0
    s.review_last_prompted_at = None
    s.first_run_at = (
        rp._now() - timedelta(days=rp.DAYS_SINCE_FIRST_RUN + 1)
    ).isoformat()
    return mem_settings


# --- the happy path ------------------------------------------------------

def test_all_gates_passing_requests_a_prompt(eligible):
    assert rp.should_request_review() is True


def test_requesting_stamps_the_time_and_counts_it(monkeypatch, eligible):
    monkeypatch.setattr(rp, "_request_windows", lambda hwnd: True)
    assert rp.maybe_request_review() is True
    s = eligible["s"]
    assert s.review_prompt_count == 1
    assert s.review_last_prompted_at is not None


def test_a_platform_that_declines_is_not_counted_as_prompted(monkeypatch, eligible):
    # If the shim could not even ask, the attempt must not burn the 120-day
    # cooldown — otherwise one unusable StoreContext costs four months.
    monkeypatch.setattr(rp, "_request_windows", lambda hwnd: False)
    assert rp.maybe_request_review() is False
    assert eligible["s"].review_prompt_count == 0
    assert eligible["s"].review_last_prompted_at is None


# --- each gate, individually --------------------------------------------

def test_direct_download_build_never_prompts(monkeypatch, eligible):
    monkeypatch.setattr(rp, "STORE_BUILD", False)
    monkeypatch.setattr(rp, "MAS_BUILD", False)
    assert rp.review_available() is False
    assert rp.should_request_review() is False


def test_session_friction_blocks(eligible):
    rp.mark_session_friction()
    assert rp.should_request_review() is False


def test_test_mode_blocks(monkeypatch, eligible):
    import app.core.client as client_mod

    monkeypatch.setattr(client_mod.client_manager, "is_production", lambda: False)
    assert rp.should_request_review() is False


def test_unlicensed_production_blocks(monkeypatch, eligible):
    import app.core.license as license_mod

    monkeypatch.setattr(license_mod, "production_allowed", lambda: False)
    assert rp.should_request_review() is False


def test_too_few_successes_blocks(eligible):
    eligible["s"].review_success_count = rp.SUCCESSES_BEFORE_PROMPT - 1
    assert rp.should_request_review() is False


def test_lifetime_cap_blocks(eligible):
    eligible["s"].review_prompt_count = rp.MAX_PROMPTS_EVER
    assert rp.should_request_review() is False


def test_too_new_an_install_blocks(eligible):
    eligible["s"].first_run_at = rp._now().isoformat()
    assert rp.should_request_review() is False


def test_recent_prompt_blocks(eligible):
    recent = rp._now() - timedelta(days=rp.DAYS_BETWEEN_PROMPTS - 1)
    eligible["s"].review_last_prompted_at = recent.isoformat()
    assert rp.should_request_review() is False


def test_an_old_enough_prompt_does_not_block(eligible):
    old = rp._now() - timedelta(days=rp.DAYS_BETWEEN_PROMPTS + 1)
    eligible["s"].review_last_prompted_at = old.isoformat()
    assert rp.should_request_review() is True


# --- first-run stamping --------------------------------------------------

def test_missing_first_run_is_stamped_now_and_blocks_this_time(mem_settings, store_build, in_production):
    """An install upgrading into the feature must start its clock at the
    upgrade. A null stamp read as 'infinitely old' would prompt everyone the
    moment they updated."""
    s = mem_settings["s"]
    s.first_run_at = None
    s.review_success_count = rp.SUCCESSES_BEFORE_PROMPT
    assert rp.should_request_review() is False
    assert mem_settings["s"].first_run_at is not None


def test_first_run_stamp_is_not_moved_once_set(mem_settings, store_build):
    original = (rp._now() - timedelta(days=400)).isoformat()
    mem_settings["s"].first_run_at = original
    rp.ensure_first_run_stamp()
    assert mem_settings["s"].first_run_at == original


def test_unparseable_first_run_is_restamped(mem_settings, store_build):
    mem_settings["s"].first_run_at = "not a date"
    stamped = rp.ensure_first_run_stamp()
    assert stamped is not None
    assert mem_settings["s"].first_run_at != "not a date"


# --- counting ------------------------------------------------------------

def test_successes_accumulate(mem_settings, store_build):
    rp.note_successful_shipment()
    rp.note_successful_shipment()
    assert mem_settings["s"].review_success_count == 2


def test_successes_are_not_counted_off_store(monkeypatch, mem_settings):
    monkeypatch.setattr(rp, "STORE_BUILD", False)
    monkeypatch.setattr(rp, "MAS_BUILD", False)
    rp.note_successful_shipment()
    assert mem_settings["s"].review_success_count == 0


# --- degradation ---------------------------------------------------------

def test_an_undeterminable_mode_fails_closed(monkeypatch, eligible):
    """If the mode cannot be read, do not prompt. Failing closed costs one
    prompt; failing open asks a test-mode user to rate an app they never bought."""
    import app.core.client as client_mod

    def boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(client_mod.client_manager, "is_production", boom)
    assert rp.should_request_review() is False


def test_platform_shims_never_raise(monkeypatch):
    """Whatever the bindings do, the shims answer False rather than propagating
    — the same guarantee the two entitlement modules make."""
    monkeypatch.setattr(rp, "STORE_BUILD", False)
    monkeypatch.setattr(rp, "MAS_BUILD", False)
    assert rp._request_windows(None) is False
    assert rp._request_macos() is False
