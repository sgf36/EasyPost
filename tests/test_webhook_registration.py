"""Webhook registration: per-mode ids, the secret on update, batch events.

A webhook belongs to one EasyPost account. Storing a single id across both
modes meant that switching mode made the app try to repoint the *other*
account's webhook, fail, and quietly create a second one — orphaning the first,
which then sat registered against a tunnel that no longer existed.
"""

from unittest.mock import Mock, patch

import pytest

from app.core import webhook_manager as WM
from app.core.settings import AppSettings


def _settings(**over):
    settings = AppSettings()
    for key, value in over.items():
        setattr(settings, key, value)
    return settings


# ---------------------------------------------------------------------------
# Per-mode registration
# ---------------------------------------------------------------------------


def test_each_mode_keeps_its_own_webhook_id():
    settings = _settings(webhook_ids={"test": "hook_test", "production": "hook_prod"})
    assert WM._webhook_id_for_mode(settings, "test") == "hook_test"
    assert WM._webhook_id_for_mode(settings, "production") == "hook_prod"


def test_a_mode_with_no_registration_yields_none():
    settings = _settings(webhook_ids={"test": "hook_test"})
    assert WM._webhook_id_for_mode(settings, "production") is None


def test_a_pre_1_2_0_single_id_is_migrated():
    """Older builds stored one id with no record of which account owned it."""
    settings = _settings(webhook_id="hook_legacy", webhook_ids={})
    assert WM._webhook_id_for_mode(settings, "test") == "hook_legacy"


def test_the_per_mode_entry_wins_over_the_legacy_id():
    settings = _settings(webhook_id="hook_legacy", webhook_ids={"test": "hook_new"})
    assert WM._webhook_id_for_mode(settings, "test") == "hook_new"


# ---------------------------------------------------------------------------
# The secret, on update as well as create
# ---------------------------------------------------------------------------


def _run_start(existing_ids, mode="test"):
    """Drive WebhookManager.start() with the tunnel and receiver stubbed out."""
    client = Mock()
    client.webhook.update.return_value = Mock(id="hook_updated")
    client.webhook.create.return_value = Mock(id="hook_created")

    manager = Mock()
    manager.get_client.return_value = client
    manager.active_mode = mode

    saved = {}
    receiver = Mock()
    receiver.start.return_value = 8123
    tunnel = Mock()
    tunnel.start.return_value = "https://example.trycloudflare.com"

    with patch.object(WM, "client_manager", manager), \
            patch.object(WM, "_get_or_create_webhook_secret", return_value="s3cret"), \
            patch.object(WM, "WebhookReceiver", return_value=receiver), \
            patch.object(WM, "CloudflareTunnel", return_value=tunnel), \
            patch.object(WM, "load_settings",
                         return_value=_settings(webhook_ids=dict(existing_ids))), \
            patch.object(WM, "save_settings", side_effect=lambda s: saved.update(
                {"ids": s.webhook_ids, "legacy": s.webhook_id})):
        WM.WebhookManager().start()

    return client, saved


def test_the_secret_is_resent_when_updating_an_existing_webhook():
    """Without it EasyPost keeps the old secret, so every incoming event fails
    signature validation with a 401 — a push feature that reports itself as
    running and silently delivers nothing."""
    client, _ = _run_start({"test": "hook_existing"})
    kwargs = client.webhook.update.call_args.kwargs
    assert kwargs["webhook_secret"] == "s3cret"
    assert kwargs["url"].endswith("/webhook")


def test_a_new_webhook_is_created_when_the_mode_has_none():
    client, saved = _run_start({})
    client.webhook.update.assert_not_called()
    client.webhook.create.assert_called_once()
    assert saved["ids"]["test"] == "hook_created"


def test_registering_one_mode_leaves_the_other_alone():
    _, saved = _run_start({"production": "hook_prod"}, mode="test")
    assert saved["ids"]["production"] == "hook_prod"
    assert saved["ids"]["test"] == "hook_created"


def test_the_legacy_single_id_is_cleared_once_migrated():
    _, saved = _run_start({})
    assert saved["legacy"] is None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_a_tracker_event_updates_tracking():
    manager = WM.WebhookManager()
    seen = []
    manager.tracker_updated.connect(seen.append)
    with patch.object(WM, "save_tracker_locally") as save:
        manager._on_event({
            "description": "tracker.updated",
            "result": {"id": "trk_1", "status": "in_transit"},
        })
    save.assert_called_once()
    assert seen == ["trk_1"]


@pytest.mark.parametrize("description", ["batch.created", "batch.updated"])
def test_a_batch_event_is_surfaced(description):
    """Batch creation and purchase are asynchronous and can take minutes, so
    without these the Batch view has nothing but its own timer to go on."""
    manager = WM.WebhookManager()
    seen = []
    manager.batch_updated.connect(seen.append)
    manager._on_event({"description": description, "result": {"id": "batch_1"}})
    assert seen == ["batch_1"]


def test_an_unrelated_event_is_ignored():
    manager = WM.WebhookManager()
    trackers, batches = [], []
    manager.tracker_updated.connect(trackers.append)
    manager.batch_updated.connect(batches.append)
    manager._on_event({"description": "payment.created", "result": {"id": "pay_1"}})
    assert not trackers and not batches


def test_an_event_with_no_id_is_ignored():
    manager = WM.WebhookManager()
    batches = []
    manager.batch_updated.connect(batches.append)
    manager._on_event({"description": "batch.updated", "result": {}})
    assert not batches
