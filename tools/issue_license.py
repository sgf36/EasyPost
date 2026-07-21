"""Mint an offline license key for Easy-Post Desktop.

Run this with the PRIVATE signing key (kept out of the repo) to issue a
license after a Paddle purchase. The app verifies the result offline with the
embedded public key (see app/core/license.py).

Usage:
    python tools/issue_license.py --key /path/to/private.pem \
        --email buyer@example.com --order PADDLE-12345

Or set the key path once via the EASYPOST_LICENSE_KEY_PATH env var and omit
--key. Prints the license key to stdout.
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PRODUCT_ID = "easypost-desktop"
FORMAT_TAG = "EPD1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def load_private_key(path: str) -> Ed25519PrivateKey:
    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path} is not an Ed25519 private key")
    return key


def mint(private_key: Ed25519PrivateKey, email: str, order: str) -> str:
    payload = {
        "v": 1,
        "product": PRODUCT_ID,
        "email": email,
        "order": order,
        "iat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_bytes)
    return f"{FORMAT_TAG}.{_b64url(payload_bytes)}.{_b64url(signature)}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mint an Easy-Post Desktop license key.")
    parser.add_argument("--key", default=os.environ.get("EASYPOST_LICENSE_KEY_PATH"),
                        help="Path to the Ed25519 private key PEM (or set EASYPOST_LICENSE_KEY_PATH).")
    parser.add_argument("--email", required=True, help="Buyer email (recorded in the license).")
    parser.add_argument("--order", default="", help="Paddle order/transaction id (optional but recommended).")
    args = parser.parse_args(argv)

    if not args.key:
        parser.error("no private key: pass --key or set EASYPOST_LICENSE_KEY_PATH")

    private_key = load_private_key(args.key)
    print(mint(private_key, args.email, args.order))
    return 0


if __name__ == "__main__":
    sys.exit(main())
