"""Verify entered EasyPost keys sit in the right slots before they are saved.

The paid gate keys off a key's TRUE mode, so the free "test" field must hold a
genuine test key and the "production" field a genuine production key. This runs
the (network) mode check off the UI thread and refuses to save on a mismatch —
so a production key cannot be smuggled into the free slot to ship for free.

Every refusal names the field it is about and says what actually went wrong.
Until 1.3.0 a typo, a key in the wrong field, a dropped connection and an
EasyPost outage all produced one message that blamed the key, so someone
offline was told to recopy a key that was fine, and someone with one bad key of
two could not tell which to fix.

Shared by the first-run setup wizard and the Settings key form.
"""

from typing import Callable, Optional

from PySide6.QtWidgets import QMessageBox, QWidget

from app.config import MODE_PRODUCTION, MODE_TEST
from app.core.easypost_keys import (
    PROBLEM_EASYPOST_ERROR,
    PROBLEM_INVALID_KEY,
    PROBLEM_NETWORK,
    KeyCheck,
    check_keys,
    mode_from_prefix,
)
from app.i18n import tr
from app.ui.widgets.async_worker import run_async

# Which field a message is about; passed to ``on_field_error``.
FIELD_TEST = MODE_TEST
FIELD_PRODUCTION = MODE_PRODUCTION

PROBLEM_WRONG_MODE = "wrong_mode"


def prefix_problem(field: str, key: str) -> Optional[str]:
    """A wrong-field key caught from its prefix alone, before any network call.

    Only ever refuses: a key whose prefix matches its field still goes to
    EasyPost, because the prefix is a claim and the API response is proof."""
    claimed = mode_from_prefix(key)
    if claimed is not None and claimed != field:
        return PROBLEM_WRONG_MODE
    return None


def slot_problem(field: str, key: str, check: KeyCheck) -> Optional[str]:
    """What is wrong with ``key`` in ``field`` given EasyPost's answer, or None
    if it belongs there. Pure, so each classification is testable without Qt
    or a network."""
    if not key or check.mode == field:
        return None
    if check.mode is not None:
        return PROBLEM_WRONG_MODE
    return check.problem or PROBLEM_EASYPOST_ERROR


# (field, problem) -> (title key, body key). The field is in the body rather
# than substituted into one shared sentence, because a translated field name
# dropped into another language's sentence rarely agrees with it.
_MESSAGES = {
    (FIELD_TEST, PROBLEM_WRONG_MODE): ("key_check.prod_in_test_title", "key_check.prod_in_test_body"),
    (FIELD_PRODUCTION, PROBLEM_WRONG_MODE): ("key_check.test_in_prod_title", "key_check.test_in_prod_body"),
    (FIELD_TEST, PROBLEM_INVALID_KEY): ("key_check.invalid_test_title", "key_check.invalid_test_body"),
    (FIELD_PRODUCTION, PROBLEM_INVALID_KEY): ("key_check.invalid_prod_title", "key_check.invalid_prod_body"),
    (FIELD_TEST, PROBLEM_NETWORK): ("key_check.network_title", "key_check.network_test_body"),
    (FIELD_PRODUCTION, PROBLEM_NETWORK): ("key_check.network_title", "key_check.network_prod_body"),
    (FIELD_TEST, PROBLEM_EASYPOST_ERROR): ("key_check.easypost_error_title", "key_check.easypost_error_test_body"),
    (FIELD_PRODUCTION, PROBLEM_EASYPOST_ERROR): ("key_check.easypost_error_title", "key_check.easypost_error_prod_body"),
}


def message_for(field: str, problem: str) -> tuple[str, str]:
    """(title, body) catalogue keys for a problem with one field."""
    return _MESSAGES.get((field, problem)) or _MESSAGES[(field, PROBLEM_EASYPOST_ERROR)]


def verify_key_slots(
    widget: QWidget,
    test_key: str,
    prod_key: str,
    on_ok: Callable[[], None],
    on_busy: Optional[Callable[[bool], None]] = None,
    on_field_error: Optional[Callable[[str], None]] = None,
) -> None:
    """Check each non-empty key's true mode with EasyPost, off the UI thread.

    Calls ``on_ok()`` only if the test field holds a test key and the
    production field a production key. Otherwise it calls
    ``on_field_error(FIELD_TEST | FIELD_PRODUCTION)`` so the caller can
    highlight that field, then shows a message about it. ``on_busy(True)`` /
    ``on_busy(False)`` bracket the network check so the caller can disable its
    buttons meanwhile.
    """
    # A key pasted into the wrong field is refused before anyone waits on the
    # network for an answer its prefix already gives.
    for field, key in ((FIELD_TEST, test_key), (FIELD_PRODUCTION, prod_key)):
        problem = prefix_problem(field, key) if key else None
        if problem:
            _report(widget, field, problem, on_field_error)
            return

    if on_busy:
        on_busy(True)

    def work():
        return check_keys(test_key, prod_key)

    def done(result) -> None:
        if on_busy:
            on_busy(False)
        test_check, prod_check = result
        for field, key, check in (
            (FIELD_TEST, test_key, test_check),
            (FIELD_PRODUCTION, prod_key, prod_check),
        ):
            problem = slot_problem(field, key, check)
            if problem:
                _report(widget, field, problem, on_field_error)
                return
        on_ok()

    def failed(_exc) -> None:
        # check_keys does not raise, so reaching here is a fault of ours, not
        # the key's: report it as an error, never as a bad key.
        if on_busy:
            on_busy(False)
        field = FIELD_TEST if test_key else FIELD_PRODUCTION
        _report(widget, field, PROBLEM_EASYPOST_ERROR, on_field_error)

    task = run_async(work, widget)
    task.succeeded.connect(done)
    task.failed.connect(failed)
    # Keep a reference so the QThread is not garbage-collected mid-flight.
    widget._key_verify_task = task


_BAD_FIELD_STYLE = "QLineEdit { border: 2px solid #c53030; border-radius: 4px; }"


def mark_field(field_widget, bad: bool) -> None:
    """Outline a key field in red, and clear the outline once the user edits it.

    The message box goes away when dismissed; the outline is what still says
    which of the two fields to fix while the user fixes it."""
    field_widget.setStyleSheet(_BAD_FIELD_STYLE if bad else "")
    if bad:
        field_widget.setFocus()
        field_widget.selectAll()
        if not getattr(field_widget, "_clears_bad_mark", False):
            field_widget.textEdited.connect(lambda _text: field_widget.setStyleSheet(""))
            field_widget._clears_bad_mark = True


def _report(
    widget: QWidget,
    field: str,
    problem: str,
    on_field_error: Optional[Callable[[str], None]],
) -> None:
    if on_field_error:
        on_field_error(field)
    title_key, body_key = message_for(field, problem)
    QMessageBox.warning(widget, tr(title_key), tr(body_key))
