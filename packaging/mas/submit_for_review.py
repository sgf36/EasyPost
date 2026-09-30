"""Submit a processed build to App Store Connect for review.

Called by CI after ``altool --upload-app`` uploads the .pkg.  Apple takes
a few minutes to process the binary before it appears in the API, so this
script polls until the build is ready, attaches it to the app store version,
sets the "What's New" text, and submits for review.

Requires the same three ASC API-key env vars the upload step already has:
    ASC_KEY_ID, ASC_ISSUER_ID, ASC_API_KEY_P8_BASE64

Usage:
    python packaging/mas/submit_for_review.py \
        --version 1.6.0 --build-number 347 \
        --whats-new "Ship with Royal Mail directly..."
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

API = "https://api.appstoreconnect.apple.com/v1"
BUNDLE_ID = "com.spencerfields.easypostdesktop"
PLATFORM = "MAC_OS"
BUILD_POLL_INTERVAL = 30
BUILD_POLL_TIMEOUT = 900  # 15 minutes


def _make_token(key_id: str, issuer_id: str, key_pem: bytes) -> str:
    """Mint a short-lived ES256 JWT for the App Store Connect API."""
    from cryptography.hazmat.primitives import hashes

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    now = int(time.time())
    payload = {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"}

    segments = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(payload).encode())

    key = load_pem_private_key(key_pem, password=None)
    der_sig = key.sign(segments.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = ec.utils.decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return segments + "." + _b64(raw_sig)


class ASCClient:
    def __init__(self, key_id: str, issuer_id: str, key_pem: bytes):
        self._token = _make_token(key_id, issuer_id, key_pem)
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        })

    def get(self, path: str, **params) -> dict:
        r = self._session.get(f"{API}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self._session.post(f"{API}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def patch(self, path: str, body: dict) -> dict:
        r = self._session.patch(f"{API}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()


def find_app(client: ASCClient) -> str:
    data = client.get("/apps", **{"filter[bundleId]": BUNDLE_ID})
    apps = data["data"]
    if not apps:
        sys.exit(f"No app found with bundle ID {BUNDLE_ID}")
    app_id = apps[0]["id"]
    print(f"App: {apps[0]['attributes']['name']} ({app_id})")
    return app_id


def wait_for_build(client: ASCClient, app_id: str, version: str, build_number: str) -> str:
    """Poll until the uploaded build finishes processing."""
    print(f"Waiting for build {version} ({build_number}) to finish processing...")
    deadline = time.time() + BUILD_POLL_TIMEOUT
    while time.time() < deadline:
        data = client.get(
            f"/builds",
            **{
                "filter[app]": app_id,
                "filter[version]": build_number,
                "filter[preReleaseVersion.version]": version,
                "filter[processingState]": "PROCESSING,VALID,INVALID",
                "limit": 1,
            },
        )
        builds = data["data"]
        if builds:
            state = builds[0]["attributes"]["processingState"]
            print(f"  Build state: {state}")
            if state == "VALID":
                build_id = builds[0]["id"]
                print(f"  Build ready: {build_id}")
                return build_id
            if state == "INVALID":
                sys.exit("Build processing failed — marked INVALID by Apple.")
        else:
            print("  Build not yet visible in API...")
        time.sleep(BUILD_POLL_INTERVAL)
    sys.exit(f"Build did not finish processing within {BUILD_POLL_TIMEOUT}s")


def find_or_create_version(client: ASCClient, app_id: str, version: str) -> str:
    """Find an editable appStoreVersion for this version string, or create one."""
    data = client.get(
        f"/apps/{app_id}/appStoreVersions",
        **{
            "filter[versionString]": version,
            "filter[platform]": PLATFORM,
        },
    )
    for v in data["data"]:
        state = v["attributes"]["appStoreState"]
        if state in (
            "PREPARE_FOR_SUBMISSION",
            "DEVELOPER_REJECTED",
            "REJECTED",
            "METADATA_REJECTED",
            "WAITING_FOR_REVIEW",
            "IN_REVIEW",
            "READY_FOR_SALE",
        ):
            print(f"  Found version {version} in state {state} ({v['id']})")
            return v["id"]

    print(f"  Creating new version {version}...")
    body = {
        "data": {
            "type": "appStoreVersions",
            "attributes": {
                "versionString": version,
                "platform": PLATFORM,
            },
            "relationships": {
                "app": {"data": {"type": "apps", "id": app_id}},
            },
        }
    }
    result = client.post("/appStoreVersions", body)
    version_id = result["data"]["id"]
    print(f"  Created version {version_id}")
    return version_id


def set_build(client: ASCClient, version_id: str, build_id: str) -> None:
    """Attach the build to the app store version."""
    body = {
        "data": {"type": "builds", "id": build_id},
    }
    r = client._session.patch(
        f"{API}/appStoreVersions/{version_id}/relationships/build",
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    print(f"  Build {build_id} attached to version {version_id}")


def set_whats_new(client: ASCClient, version_id: str, whats_new: str) -> None:
    """Set the 'What's New' text on the en-US localisation."""
    data = client.get(f"/appStoreVersions/{version_id}/appStoreVersionLocalizations")
    en_loc = None
    for loc in data["data"]:
        if loc["attributes"]["locale"] == "en-US":
            en_loc = loc["id"]
            break

    if en_loc:
        client.patch(f"/appStoreVersionLocalizations/{en_loc}", {
            "data": {
                "type": "appStoreVersionLocalizations",
                "id": en_loc,
                "attributes": {"whatsNew": whats_new},
            }
        })
        print("  Updated 'What's New' on en-US")
    else:
        print("  WARNING: en-US localisation not found, skipping What's New")


def submit_for_review(client: ASCClient, version_id: str) -> None:
    """Create the submission — this sends the version to Apple's review queue."""
    body = {
        "data": {
            "type": "appStoreVersionSubmissions",
            "relationships": {
                "appStoreVersion": {
                    "data": {"type": "appStoreVersions", "id": version_id},
                },
            },
        }
    }
    try:
        client.post("/appStoreVersionSubmissions", body)
        print("  Submitted for App Review.")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 409:
            print("  Already submitted (409 Conflict) — nothing to do.")
        else:
            raise


def main() -> int:
    p = argparse.ArgumentParser(description="Submit a MAS build for App Review")
    p.add_argument("--version", required=True, help="Marketing version (e.g. 1.6.0)")
    p.add_argument("--build-number", required=True, help="CFBundleVersion (github.run_number)")
    p.add_argument("--whats-new", required=True, help="'What's New' text for the listing")
    args = p.parse_args()

    key_id = os.environ.get("ASC_KEY_ID")
    issuer_id = os.environ.get("ASC_ISSUER_ID")
    key_b64 = os.environ.get("ASC_API_KEY_P8_BASE64")
    if not all([key_id, issuer_id, key_b64]):
        sys.exit("ASC_KEY_ID, ASC_ISSUER_ID, and ASC_API_KEY_P8_BASE64 must be set")

    key_pem = base64.b64decode(key_b64)
    client = ASCClient(key_id, issuer_id, key_pem)

    app_id = find_app(client)
    build_id = wait_for_build(client, app_id, args.version, args.build_number)
    version_id = find_or_create_version(client, app_id, args.version)
    set_build(client, version_id, build_id)
    set_whats_new(client, version_id, args.whats_new)
    submit_for_review(client, version_id)

    print("\nMac App Store submission complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
