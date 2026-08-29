"""Scheduling half of the in-app review prompt.

The decision logic and the platform calls live in ``app/core/review_prompt.py``,
which is deliberately Qt-free so the gates can be tested without a UI. This is
the thin Qt wrapper that decides *when* to let it run.

Two behaviours belong here rather than in core:

- **A short delay.** Firing the moment a label is bought races the label render
  and can land on top of a print dialog. Two seconds lets the success settle, so
  the prompt arrives at the moment of satisfaction rather than in the middle of
  it.
- **A focus check at fire time, not at schedule time.** If the user has switched
  away in those two seconds they are no longer looking at their new label, and a
  prompt into a background window is both useless and irritating. There will be
  another shipment.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from app.core.review_prompt import maybe_request_review, review_available

# Long enough for the success UI to settle, short enough to still read as part
# of the same moment.
_DELAY_MS = 2000


def _window_handle(widget: QWidget) -> int:
    """HWND of the top-level window, for the Store's modal review dialog.

    Mirrors ``store_unlock._window_handle``. Zero is a fine answer — the caller
    treats it as "no owner window" rather than an error.
    """
    try:
        return int(widget.window().winId())
    except Exception:
        return 0


def schedule_review_prompt(widget: QWidget) -> None:
    """Consider asking for a review, shortly after a success.

    Safe to call from any success path on any build: it returns immediately on
    builds with no storefront to review on, and every other gate is applied in
    ``core.review_prompt`` when the timer fires.
    """
    if not review_available():
        return

    def fire() -> None:
        try:
            # Re-check at fire time. The window may have closed, or the user may
            # have moved to another application, in the intervening seconds.
            if widget.isVisible() and widget.window().isActiveWindow():
                maybe_request_review(_window_handle(widget))
        except RuntimeError:
            # The widget was destroyed while the timer was pending. Nothing to
            # do, and certainly nothing to report.
            pass

    QTimer.singleShot(_DELAY_MS, fire)
