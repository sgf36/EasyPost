import hashlib
import hmac
import json
import socket
import threading
import time
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
    # do_POST calls on_event() after send_response()/end_headers(), in the
    # server's request-handling thread. There's no barrier guaranteeing that
    # call completes before urlopen() returns in this (client) thread, so
    # asserting on `received` immediately after the HTTP call is a race —
    # wait on an Event the callback sets instead of relying on timing.
    received = []
    event_processed = threading.Event()

    def on_event(event):
        received.append(event)
        event_processed.set()

    receiver = WebhookReceiver(webhook_secret=SECRET, on_event=on_event)
    port = receiver.start(port=0)
    try:
        body = json.dumps({"description": "tracker.updated", "result": {"id": "trk_123"}}).encode()
        status = _post(port, WEBHOOK_PATH, body, _sign(body))
        assert status == 200
        assert event_processed.wait(timeout=5), "on_event was not called within timeout"
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


def test_rejected_request_answers_even_when_the_body_arrives_late():
    """A response must reach the client even if the body is still in flight.

    do_POST used to answer 404 without reading the request body. Bytes left
    unread in the socket turn the subsequent close into an RST rather than a
    FIN, and the client then sees the connection abort instead of the status
    that was already written to it.

    Whether that happened depended on whether the body landed in the same TCP
    segment as the headers, so through urllib it was a flake of roughly one run
    in thirty — rare enough to look like noise, and permanent for any client
    that separates the two. Sending the headers, pausing, then sending the body
    makes it deterministic: before the fix this failed every time.
    """
    receiver = WebhookReceiver(webhook_secret=SECRET, on_event=lambda e: None)
    port = receiver.start(port=0)
    try:
        body = b"{}"
        head = (
            "POST /not-a-webhook HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode()
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(head)
            time.sleep(0.15)          # force the body into a separate segment
            sock.sendall(body)
            received = b""
            while b"\r\n\r\n" not in received:
                chunk = sock.recv(4096)   # ConnectionAbortedError before the fix
                if not chunk:
                    break
                received += chunk
        finally:
            sock.close()
        assert received, "server closed without sending a response"
        assert b"404" in received.split(b"\r\n", 1)[0]
    finally:
        receiver.stop()
