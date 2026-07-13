import hashlib
import hmac
import json
import urllib.error
import urllib.request

from app.core.http_receiver import WEBHOOK_PATH, WebhookReceiver

SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(key=secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    return "hmac-sha256-hex=" + digest.hexdigest()


def _post(port: int, path: str, body: bytes, signature: str | None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST"
    )
    if signature is not None:
        req.add_header("X-Hmac-Signature", signature)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_valid_signature_returns_200_and_dispatches_event():
    received = []
    receiver = WebhookReceiver(webhook_secret=SECRET, on_event=received.append)
    port = receiver.start(port=0)
    try:
        body = json.dumps({"description": "tracker.updated", "result": {"id": "trk_123"}}).encode()
        status = _post(port, WEBHOOK_PATH, body, _sign(body))
        assert status == 200
        assert received == [{"description": "tracker.updated", "result": {"id": "trk_123"}}]
    finally:
        receiver.stop()


def test_invalid_signature_returns_401_and_does_not_dispatch():
    received = []
    receiver = WebhookReceiver(webhook_secret=SECRET, on_event=received.append)
    port = receiver.start(port=0)
    try:
        body = json.dumps({"description": "tracker.updated"}).encode()
        status = _post(port, WEBHOOK_PATH, body, _sign(body, secret="wrong-secret"))
        assert status == 401
        assert received == []
    finally:
        receiver.stop()


def test_missing_signature_returns_401():
    receiver = WebhookReceiver(webhook_secret=SECRET, on_event=lambda e: None)
    port = receiver.start(port=0)
    try:
        status = _post(port, WEBHOOK_PATH, b"{}", None)
        assert status == 401
    finally:
        receiver.stop()


def test_wrong_path_returns_404():
    receiver = WebhookReceiver(webhook_secret=SECRET, on_event=lambda e: None)
    port = receiver.start(port=0)
    try:
        body = b"{}"
        status = _post(port, "/not-a-webhook", body, _sign(body))
        assert status == 404
    finally:
        receiver.stop()
