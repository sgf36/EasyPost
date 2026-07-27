"""Verify entered EasyPost keys sit in the right slots before they are saved.

The paid gate keys off a key's TRUE mode, so the free "test" field must hold a
genuine test key and the "production" field a genuine production key. This runs
the (network) mode check off the UI thread and refuses to save on a mismatch —
so a production key cannot be smuggled into the free slot to ship for free.

Shared by the first-run setup wizard and the Settings key form.
"""

from typing import Callable, Optional

from PySide6.QtWidgets import QMessageBox, QWidget

from app.config import MODE_PRODUCTION, MODE_TEST
from app.core.easypost_keys import detect_mode
from app.i18n import tr
from app.ui.widgets.async_worker import run_async


def verify_key_slots(
    widget: QWidget,
    test_key: str,
    prod_key: str,
    on_ok: Callable[[], None],
    on_busy: Optional[Callable[[bool], None]] = None,
) -> None:
    """Check each non-empty key's true mode with EasyPost, off the UI thread.

    Calls ``on_ok()`` only if the test field holds a test key and the
    production field a production key; otherwise shows a clear message and does
    nothing. ``on_busy(True)`` / ``on_busy(False)`` bracket the network check so
    the caller can disable its buttons meanwhile.
    """
    if on_busy:
        on_busy(True)

    def work():
        return (
            detect_mode(test_key) if test_key else None,
            detect_mode(prod_key) if prod_key else None,
        )

    def done(result) -> None:
        if on_busy:
            on_busy(False)
        test_mode, prod_mode = result
        if test_key and test_mode != MODE_TEST:
            _warn(widget, test_mode, is_test_slot=True)
            return
        if prod_key and prod_mode != MODE_PRODUCTION:
            _warn(widget, prod_mode, is_test_slot=False)
            return
        on_ok()

    def failed(_exc) -> None:
        if on_busy:
            on_busy(False)
        _unverifiable(widget)

    task = run_async(work, widget)
    task.succeeded.connect(done)
    task.failed.connect(failed)
    # Keep a reference so the QThread is not garbage-collected mid-flight.
    widget._key_verify_task = task


def _warn(widget: QWidget, detected: Optional[str], is_test_slot: bool) -> None:
    if is_test_slot and detected == MODE_PRODUCTION:
        QMessageBox.warning(
            widget, tr("key_check.prod_in_test_title"), tr("key_check.prod_in_test_body")
        )
    elif not is_test_slot and detected == MODE_TEST:
        QMessageBox.warning(
            widget, tr("key_check.test_in_prod_title"), tr("key_check.test_in_prod_body")
        )
    else:
        _unverifiable(widget)


def _unverifiable(widget: QWidget) -> None:
    QMessageBox.warning(
        widget, tr("key_check.unverifiable_title"), tr("key_check.unverifiable_body")
    )
