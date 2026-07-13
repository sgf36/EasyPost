import pytest
from PySide6.QtCore import QCoreApplication

from app.core.webhook_manager import WebhookManager, _get_or_create_webhook_secret


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    # QObject/Signal machinery needs a QCoreApplication instance to exist,
    # even for headless unit tests with no event loop running.
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def test_webhook_secret_is_generated_once_and_reused(monkeypatch):
    store = {}
    monkeypatch.setattr(
        "app.core.webhook_manager.keyring.get_password",
        lambda service, username: store.get((service, username)),
    )

    def fake_set(service, username, value):
        store[(service, username)] = value

    monkeypatch.setattr("app.core.webhook_manager.keyring.set_password", fake_set)

    first = _get_or_create_webhook_secret()
    second = _get_or_create_webhook_secret()

    assert first == second
    assert len(first) > 20


def test_on_event_ignores_non_tracker_events(monkeypatch):
    saved = []
    monkeypatch.setattr("app.core.webhook_manager.save_tracker_locally", saved.append)

    manager = WebhookManager()
    emitted = []
    manager.tracker_updated.connect(emitted.append)

    manager._on_event({"description": "batch.created", "result": {"id": "batch_1"}})

    assert saved == []
    assert emitted == []


def test_on_event_dispatches_valid_tracker_update(monkeypatch):
    saved = []
    monkeypatch.setattr("app.core.webhook_manager.save_tracker_locally", saved.append)

    manager = WebhookManager()
    emitted = []
    manager.tracker_updated.connect(emitted.append)

    tracker_payload = {"id": "trk_123", "status": "in_transit"}
    manager._on_event({"description": "tracker.updated", "result": tracker_payload})

    assert saved == [tracker_payload]
    assert emitted == ["trk_123"]


def test_on_event_ignores_tracker_update_missing_id(monkeypatch):
    saved = []
    monkeypatch.setattr("app.core.webhook_manager.save_tracker_locally", saved.append)

    manager = WebhookManager()
    manager._on_event({"description": "tracker.updated", "result": {"status": "in_transit"}})

    assert saved == []
