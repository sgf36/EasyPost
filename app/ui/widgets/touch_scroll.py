"""Finger, pen, and two-finger-touchpad scrolling across the app.

Two separate things have to work, and Qt gives us neither for free in the way a
user expects:

1. **Touchscreen / pen finger-drag.** Qt scrolls a ``QAbstractScrollArea`` with
   the mouse wheel out of the box, but it does not let a touchscreen finger drag
   the content. ``QScroller`` with a ``TouchGesture`` adds that, responding only
   to real touch points — mouse clicks, selection, and wheel scrolling are left
   exactly as they are.

2. **Two-finger touchpad over a non-scrolling panel.** A touchpad's two-finger
   scroll reaches the app as wheel events. A ``QAbstractScrollArea`` that is
   deliberately not scrollable (its vertical scrollbar switched off — e.g. the
   fixed-height Rates table, sized to its content) still *consumes* those wheel
   events and does nothing, so the page underneath it will not move while the
   pointer is over it. The fix is to bubble the wheel up to the nearest ancestor
   that can actually scroll.

:func:`enable_touch_scrolling_tree` applies both, once, over the whole widget
tree.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QScroller


def _closest_scrollable_ancestor(widget) -> QAbstractScrollArea | None:
    """The nearest ancestor scroll area that can actually scroll vertically."""
    node = widget.parentWidget() if widget is not None else None
    while node is not None:
        if isinstance(node, QAbstractScrollArea):
            bar = node.verticalScrollBar()
            if (
                node.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                and bar is not None
                and bar.maximum() > bar.minimum()
            ):
                return node
        node = node.parentWidget()
    return None


class _WheelBubbleFilter(QObject):
    """Forwards wheel events from a deliberately-non-scrolling viewport up to the
    nearest scrollable ancestor, so a two-finger/wheel scroll over it still moves
    the page. If nothing above can scroll, the event is left untouched."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel:
            area = _closest_scrollable_ancestor(obj)
            if area is not None:
                QApplication.sendEvent(area.viewport(), event)
                return True
        return False


def enable_touch_scrolling(area: QAbstractScrollArea) -> None:
    """Enable finger/pen kinetic scrolling on one scroll area's viewport, and —
    if the area is deliberately not vertically scrollable — make its wheel events
    bubble to the page behind it."""
    viewport = area.viewport()
    if viewport is None:
        return
    QScroller.grabGesture(viewport, QScroller.ScrollerGestureType.TouchGesture)
    if area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
        # Parent the filter to the area so it lives as long as the widget does.
        bubbler = _WheelBubbleFilter(area)
        viewport.installEventFilter(bubbler)


def enable_touch_scrolling_tree(root) -> None:
    """Enable finger/pen scrolling — and no-scroll wheel bubbling — on every
    scroll area under ``root`` (and on ``root`` itself if it is one). Best-effort
    and idempotent; a widget that objects is simply skipped."""
    areas = list(root.findChildren(QAbstractScrollArea))
    if isinstance(root, QAbstractScrollArea):
        areas.append(root)
    seen: set[int] = set()
    for area in areas:
        if id(area) in seen:
            continue
        seen.add(id(area))
        try:
            enable_touch_scrolling(area)
        except Exception:
            # A scrolling nicety must never be the thing that stops the window
            # opening; skip anything that objects.
            pass
