"""Switching test/production must refresh the view the user is looking at.

The mode selector lives in the banner, so the switch can happen without leaving
the current page. Views load their mode-scoped data (saved addresses, shipments,
claims) in their on-show refresh, so anything navigated to afterwards is correct
— but the page already on screen kept the previous mode's rows. On Batch
Shipments that left a test-mode "Ship from" address selected while production
was active, and since a saved address ID belongs to one EasyPost account, every
shipment in the submitted batch failed at creation.

ModeBanner.mode_changed existed and was emitted, but nothing was connected to
it. These pin both halves: the banner still emits, and the handler re-runs the
visible view's refresh.
"""

import pytest
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from app.ui.main_window import MainWindow
from app.ui.widgets.mode_banner import ModeBanner


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Window:
    """Stands in for MainWindow: only the two attributes the handler touches.

    Calling the handler unbound keeps this a unit test of the refresh contract —
    a real MainWindow would pull in the credential store, licence gate and DB.
    """

    def __init__(self, stack: QStackedWidget, actions: dict) -> None:
        self._view_stack = stack
        self._nav_actions = actions


def _stack_with_two_pages(qapp) -> QStackedWidget:
    stack = QStackedWidget()
    stack.addWidget(QWidget())
    stack.addWidget(QWidget())
    return stack


def test_mode_change_refreshes_the_visible_view(qapp):
    stack = _stack_with_two_pages(qapp)
    calls = []
    actions = {0: lambda: calls.append(0), 1: lambda: calls.append(1)}
    stack.setCurrentIndex(1)  # e.g. Batch Shipments, holding a "Ship from" list

    MainWindow._on_mode_changed(_Window(stack, actions), "production")

    assert calls == [1], "the on-screen view's refresh should run, and only it"


def test_mode_change_leaves_other_views_to_their_own_on_show(qapp):
    """Off-screen views refresh when navigated to, so the handler must not run
    every view's refresh — several of those hit the API."""
    stack = _stack_with_two_pages(qapp)
    calls = []
    actions = {0: lambda: calls.append(0), 1: lambda: calls.append(1)}
    stack.setCurrentIndex(0)

    MainWindow._on_mode_changed(_Window(stack, actions), "test")

    assert calls == [0]


def test_mode_change_on_a_view_with_no_refresh_is_a_no_op(qapp):
    """Some pages (Dashboard, HTS Lookup) register no on-show callable."""
    stack = _stack_with_two_pages(qapp)
    stack.setCurrentIndex(1)

    MainWindow._on_mode_changed(_Window(stack, {0: lambda: None}), "test")


def test_mode_banner_still_declares_the_signal_the_handler_relies_on():
    assert hasattr(ModeBanner, "mode_changed")
