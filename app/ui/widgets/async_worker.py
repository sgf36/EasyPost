"""Run a blocking call (EasyPost API request) off the Qt UI thread.

Every view that hits the network (address verification, rate shopping,
buying labels, tracking refresh, ...) needs this so the window doesn't
freeze mid-request. Usage:

    worker = run_async(lambda: verify_address(**fields), self)
    worker.succeeded.connect(on_success)
    worker.failed.connect(on_error)
"""

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class _Worker(QObject):
    succeeded = Signal(object)
    failed = Signal(Exception)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(exc)
        else:
            self.succeeded.emit(result)


class AsyncTask(QObject):
    succeeded = Signal(object)
    failed = Signal(Exception)

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = _Worker(fn)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_finished)
        self._worker.failed.connect(self._on_finished)
        self._worker.succeeded.connect(self.succeeded)
        self._worker.failed.connect(self.failed)

    def start(self) -> None:
        self._thread.start()

    def _on_finished(self, _payload: object = None) -> None:
        self._thread.quit()
        self._thread.wait()


def run_async(fn: Callable[[], Any], parent: QObject | None = None) -> AsyncTask:
    task = AsyncTask(fn, parent)
    task.start()
    return task
