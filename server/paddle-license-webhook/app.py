"""Paddle -> Easy-Post Desktop license webhook.

On a completed Paddle transaction for the license price, this service mints an
Ed25519-signed offline license key (the same format app/core/license.py
verifies) and emails it to the buyer. Fully automated fulfillment.

Security model:
  - The signing PRIVATE key lives only in the LICENSE_PRIVATE_KEY_PEM env var
    (never in the repo).
  - Paddle webhooks are verified with PADDLE_WEBHOOK_SECRET before anything is
    minted, so only genuine Paddle events can issue licenses.

Required environment variables (see .env.example):
  LICENSE_PRIVATE_KEY_PEM  Ed25519 private key, PEM (PKCS8).
  PADDLE_WEBHOOK_SECRET    Signing secret of the Paddle notification destination.
  PADDLE_API_KEY           Paddle API key (used to look up the buyer's email).
  PADDLE_PRICE_ID          Only mint for this price id (pri_...).
  RESEND_API_KEY           Resend API key for sending the email.
  LICENSE_FROM_EMAIL       Verified "from" address, e.g. licenses@yourdomain.com
Optional:
  PADDLE_API_BASE          Default https://api.paddle.com (sandbox: https://sandbox-api.paddle.com)
  LICENSE_PRODUCT_ID       Default "easypost-desktop" (must match the app).
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Header, HTTPException, Request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("paddle-license-webhook")

PRODUCT_ID = os.environ.get("LICENSE_PRODUCT_ID", "easypost-desktop")
FORMAT_TAG = "EPD1"
PADDLE_API_BASE = os.environ.get("PADDLE_API_BASE", "https://api.paddle.com")
SIGNATURE_TOLERANCE_SECONDS = 300  # reject webhooks older than 5 minutes

app = FastAPI(title="Easy-Post Desktop license webhook")


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_private_key() -> Ed25519PrivateKey:
    pem = _env("LICENSE_PRIVATE_KEY_PEM").encode("utf-8")
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("LICENSE_PRIVATE_KEY_PEM is not an Ed25519 private key")
    return key


def mint_license(email: str, order: str, issued_at: str) -> str:
    payload = {"v": 1, "product": PRODUCT_ID, "email": email, "order": order, "iat": issued_at}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = _load_private_key().sign(payload_bytes)
    return f"{FORMAT_TAG}.{_b64url(payload_bytes)}.{_b64url(signature)}"


def verify_paddle_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """Paddle-Signature: 'ts=<unix>;h1=<hex hmac of "ts:body">'."""
    try:
        parts = dict(kv.split("=", 1) for kv in signature_header.split(";"))
        ts, h1 = parts["ts"], parts["h1"]
    except (ValueError, KeyError):
        return False
    if abs(time.time() - int(ts)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = f"{ts}:".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)


def get_customer_email(customer_id: str) -> str:
    resp = httpx.get(
        f"{PADDLE_API_BASE}/customers/{customer_id}",
        headers={"Authorization": f"Bearer {_env('PADDLE_API_KEY')}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"]["email"]


def send_license_email(to_email: str, license_key: str) -> None:
    body = (
        "Thank you for buying Easy-Post Desktop.\n\n"
        "Your license key:\n\n"
        f"{license_key}\n\n"
        "To activate: open Easy-Post Desktop, paste this key on the activation "
        "screen, and click Activate. Keep this email for your records.\n\n"
        "Questions? https://github.com/sgf36/EasyPost/issues\n"
    )
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {_env('RESEND_API_KEY')}"},
        json={
            "from": _env("LICENSE_FROM_EMAIL"),
            "to": [to_email],
            "subject": "Your Easy-Post Desktop license key",
            "text": body,
        },
        timeout=15,
    )
    resp.raise_for_status()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/paddle/webhook")
async def paddle_webhook(request: Request, paddle_signature: str = Header(default="")):
    raw = await request.body()
    if not verify_paddle_signature(raw, paddle_signature, _env("PADDLE_WEBHOOK_SECRET")):
        log.warning("rejected webhook: bad signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    event = json.loads(raw)
    if event.get("event_type") != "transaction.completed":
        return {"ignored": event.get("event_type")}

    data = event.get("data", {})
    price_ids = [i.get("price", {}).get("id") for i in data.get("items", [])]
    wanted = _env("PADDLE_PRICE_ID")
    if wanted not in price_ids:
        log.info("transaction %s not for our price; ignoring", data.get("id"))
        return {"ignored": "other-price"}

    txn_id = data.get("id", "")
    # Deterministic issued-at from the event so Paddle retries mint the same key.
    issued_at = (event.get("occurred_at") or "").replace("+00:00", "Z")[:20] or "1970-01-01T00:00:00Z"
    email = get_customer_email(data["customer_id"])

    license_key = mint_license(email, txn_id, issued_at)
    send_license_email(email, license_key)
    log.info("issued + emailed license for txn %s to %s", txn_id, email)
    return {"status": "license_issued", "transaction": txn_id}
