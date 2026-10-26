"""Submit an MSIX package to the Microsoft Store via Partner Center API.

Called by CI after the Windows build signs the MSIX.  The script creates a
new submission (cloned from the last published one), uploads the MSIX,
updates the "What's New" text across all listing locales, and commits for
certification.

Requires an Azure AD app associated with the Partner Center account
(Manager role) — see CI-STORE-SUBMISSION-SETUP.md for the one-time setup.

    PARTNER_CENTER_TENANT_ID      Azure AD tenant ID
    PARTNER_CENTER_CLIENT_ID      Azure AD app client ID
    PARTNER_CENTER_CLIENT_SECRET  Azure AD app client secret (key)

Usage:
    python packaging/submit_to_store.py \
        --msix dist/EasyPostDesktop.msix \
        --version 1.7.0 \
        --whats-new "Print shipping labels directly onto envelopes..."
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import zipfile

import requests

API = "https://manage.devcenter.microsoft.com/v1.0/my"
TOKEN_ENDPOINT = "https://login.microsoftonline.com/{}/oauth2/token"
TOKEN_RESOURCE = "https://manage.devcenter.microsoft.com"

COMMIT_POLL_INTERVAL = 30
COMMIT_POLL_TIMEOUT = 600


def _get_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Obtain an Azure AD access token for the Store submission API."""
    r = requests.post(
        TOKEN_ENDPOINT.format(tenant_id),
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "resource": TOKEN_RESOURCE,
        },
        timeout=30,
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    print("Obtained Azure AD access token.")
    return token


class StoreClient:
    def __init__(self, token: str):
        self._s = requests.Session()
        self._s.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "EasyPost-CI/1.0",
        })

    def get(self, path: str) -> dict:
        r = self._s.get(f"{API}{path}", timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: str = "") -> dict:
        r = self._s.post(
            f"{API}{path}",
            data=body.encode() if body else b"",
            timeout=60,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def put(self, path: str, body: dict) -> dict:
        r = self._s.put(f"{API}{path}", json=body, timeout=120)
        r.raise_for_status()
        return r.json() if r.content else {}

    def delete(self, path: str) -> None:
        r = self._s.delete(f"{API}{path}", timeout=30)
        r.raise_for_status()


def find_app(client: StoreClient, app_id: str | None) -> tuple[str, str]:
    """Return (applicationId, appName).

    If *app_id* is given, fetch that app directly.  Otherwise list all apps
    and return the first one (the account has a single app).
    """
    if app_id:
        data = client.get(f"/applications/{app_id}")
        name = data.get("primaryName", app_id)
        print(f"App: {name} ({app_id})")
        return app_id, name

    data = client.get("/applications")
    apps = data.get("value", [])
    if not apps:
        sys.exit("No apps found in this Partner Center account.")
    if len(apps) == 1:
        app = apps[0]
        aid = app["id"]
        name = app.get("primaryName", aid)
        print(f"App: {name} ({aid})")
        return aid, name

    print("Multiple apps found — pass --app-id to select one:")
    for app in apps:
        print(f"  {app['id']}  {app.get('primaryName', '(unnamed)')}")
    sys.exit(1)


def delete_pending(client: StoreClient, app_id: str, app_data: dict) -> None:
    """Delete any in-progress submission so we can create a new one."""
    pending = app_data.get("pendingApplicationSubmission")
    if pending:
        sub_id = pending["id"]
        print(f"  Deleting pending submission {sub_id}...")
        client.delete(f"/applications/{app_id}/submissions/{sub_id}")
        print("  Deleted.")


def create_submission(client: StoreClient, app_id: str) -> dict:
    """Create a new submission (cloned from the last published one)."""
    print("Creating new submission (clone of last published)...")
    data = client.post(f"/applications/{app_id}/submissions")
    sub_id = data["id"]
    print(f"  Submission: {sub_id}")
    return data


def update_packages(submission: dict, msix_filename: str) -> None:
    """Mark old packages for deletion and add the new MSIX."""
    packages = submission.get("applicationPackages", [])
    for pkg in packages:
        pkg["fileStatus"] = "PendingDelete"

    packages.append({
        "fileName": msix_filename,
        "fileStatus": "PendingUpload",
        "minimumDirectXVersion": "None",
        "minimumSystemRam": "None",
    })
    submission["applicationPackages"] = packages


def update_release_notes(submission: dict, whats_new: str) -> None:
    """Set the 'What's New' text on every locale in the listing."""
    listings = submission.get("listings", {})
    count = 0
    for locale, listing in listings.items():
        base = listing.get("baseListing", {})
        base["releaseNotes"] = whats_new
        count += 1
    print(f"  Updated releaseNotes in {count} locale(s).")


def upload_package(file_upload_url: str, msix_path: str) -> None:
    """Upload the MSIX inside a ZIP to the SAS blob URL."""
    print(f"  Packaging {msix_path} into ZIP for upload...")
    msix_filename = os.path.basename(msix_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.write(msix_path, msix_filename)
    zip_bytes = buf.getvalue()
    size_mb = len(zip_bytes) / (1024 * 1024)
    print(f"  ZIP size: {size_mb:.1f} MB. Uploading to Azure Blob Storage...")

    r = requests.put(
        file_upload_url,
        data=zip_bytes,
        headers={
            "x-ms-blob-type": "BlockBlob",
            "Content-Type": "application/octet-stream",
        },
        timeout=300,
    )
    r.raise_for_status()
    print("  Upload complete.")


def commit_submission(client: StoreClient, app_id: str, sub_id: str) -> None:
    """Commit the submission and poll until processing completes."""
    print("Committing submission...")
    client.post(f"/applications/{app_id}/submissions/{sub_id}/commit")
    print("  Committed. Polling for status...")

    deadline = time.time() + COMMIT_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(COMMIT_POLL_INTERVAL)
        status_data = client.get(
            f"/applications/{app_id}/submissions/{sub_id}/status"
        )
        status = status_data.get("status", "Unknown")
        print(f"  Status: {status}")

        if status == "CommitStarted":
            continue
        if status in ("PreProcessing", "Certification", "Publishing",
                      "Published", "PendingPublication", "Release"):
            print("  Submission accepted for processing.")
            return
        if status in ("CommitFailed", "PreProcessingFailed",
                      "CertificationFailed", "PublishFailed",
                      "ReleaseFailed"):
            details = status_data.get("statusDetails", {})
            errors = details.get("errors", [])
            for err in errors:
                print(f"  ERROR [{err.get('code')}]: {err.get('details')}")
            sys.exit(f"Submission failed with status: {status}")

    sys.exit(
        f"Submission did not finish processing within {COMMIT_POLL_TIMEOUT}s. "
        f"Check Partner Center for submission {sub_id}."
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Submit an MSIX to the Microsoft Store"
    )
    p.add_argument(
        "--msix", required=True,
        help="Path to the signed MSIX package",
    )
    p.add_argument(
        "--version", required=True,
        help="Marketing version (e.g. 1.7.0)",
    )
    p.add_argument(
        "--whats-new", required=True,
        help="'What's New' text for the store listing",
    )
    p.add_argument(
        "--app-id",
        default=os.environ.get("PARTNER_CENTER_APP_ID"),
        help="Partner Center application ID (or set PARTNER_CENTER_APP_ID)",
    )
    args = p.parse_args()

    if not os.path.isfile(args.msix):
        sys.exit(f"MSIX not found: {args.msix}")

    tenant_id = os.environ.get("PARTNER_CENTER_TENANT_ID")
    client_id = os.environ.get("PARTNER_CENTER_CLIENT_ID")
    client_secret = os.environ.get("PARTNER_CENTER_CLIENT_SECRET")
    if not all([tenant_id, client_id, client_secret]):
        sys.exit(
            "PARTNER_CENTER_TENANT_ID, PARTNER_CENTER_CLIENT_ID, and "
            "PARTNER_CENTER_CLIENT_SECRET must be set."
        )

    token = _get_token(tenant_id, client_id, client_secret)
    client = StoreClient(token)

    app_id, app_name = find_app(client, args.app_id)

    app_data = client.get(f"/applications/{app_id}")
    delete_pending(client, app_id, app_data)

    submission = create_submission(client, app_id)
    sub_id = submission["id"]
    file_upload_url = submission["fileUploadUrl"]

    msix_filename = os.path.basename(args.msix)
    update_packages(submission, msix_filename)
    update_release_notes(submission, args.whats_new)

    print(f"Updating submission {sub_id}...")
    client.put(f"/applications/{app_id}/submissions/{sub_id}", submission)
    print("  Submission data updated.")

    upload_package(file_upload_url, args.msix)
    commit_submission(client, app_id, sub_id)

    print(f"\nMicrosoft Store submission complete for {app_name} v{args.version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
