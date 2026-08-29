"""App Store analytics for the two Easy-Post apps — the ongoing report requests.

    python tools/asc_analytics.py              # show both requests and any reports
    python tools/asc_analytics.py --create     # start ONGOING requests (once per app)

Impressions, product page views, downloads and the *source* of each — the numbers
that answer "is the listing being surfaced at all", which is the top question in
marketing/README.md §7.

**None of it can be backfilled.** Apple accrues data from the day the request is
created and not one day earlier, so a request that does not exist is a permanent
hole rather than a delayed report. That is why this exists.

## The key here is NOT the submission key

Every other Apple script uses the App Manager key `4CU796U485`, which is right for
builds and metadata and is refused here:

    POST analyticsReportRequests -> 403 "The API key in use does not allow this request"

That 403 is about the key's *role*. A bare `GET analyticsReportRequests` gives a
different 403 —

    403 "The resource does not allow GET_COLLECTION. Allowed operations are:
    CREATE, DELETE, GET_INSTANCE"

— which is about the resource's *shape*. There is no listing endpoint at all, so
**the request ids below are the only handle on them**; losing one means creating a
duplicate rather than finding the original. Record them here when created.

`ZMTWC3PTN6` carries the role this needs. See memory reference-app-store-connect-api.
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

import jwt

KEY_ID = "ZMTWC3PTN6"
ISSUER = "65aee88f-46c4-4daf-8238-5dc37263d06b"
KEY = pathlib.Path(
    r"C:\Users\SpencerFields\OneDrive - Spencer Fields"
    r"\Apps\Claude MacOS\signing\AuthKey_ZMTWC3PTN6.p8")

# There is no way to list requests, so these ids ARE the record. Fill each in
# after --create and do not lose them.
APPS = {
    # Both created 2026-08-28. Data accrues from that date and no earlier.
    "Easy-Post Desktop (macOS)": {
        "app": "6797912453",
        "request": "586fb7d2-b137-48c7-bb12-7b0c7ff5a1d7",
    },
    "Easy-Post Mobile Companion (iOS)": {
        "app": "6798723985",
        "request": "d4ebcad3-7530-4d98-96ab-528f18360a71",
    },
}

BASE = "https://api.appstoreconnect.apple.com/v1"


def token():
    # iat backdated 60s and exp pulled back to match: Apple rejects a token
    # issued at exactly now, AND caps lifetime at 20 minutes measured as
    # exp - iat. Missing either constraint gives intermittent 401s.
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER, "iat": now - 60, "exp": now + 1140,
         "aud": "appstoreconnect-v1"},
        KEY.read_text(), algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"})


def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {token()}"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE}/{path}", data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            return e.code, {}


def create():
    rc = 0
    for label, cfg in APPS.items():
        print(f"\n{label}")
        if cfg["request"]:
            print(f"  already recorded: {cfg['request']}")
            print("  Refusing to create a second — there is no way to list or "
                  "find a duplicate.")
            continue
        status, payload = call("analyticsReportRequests", "POST", {
            "data": {"type": "analyticsReportRequests",
                     "attributes": {"accessType": "ONGOING"},
                     "relationships": {"app": {"data": {"type": "apps",
                                                        "id": cfg["app"]}}}}})
        if status != 201:
            print(f"  refused, HTTP {status}")
            print(json.dumps(payload, indent=2)[:800])
            rc = 1
            continue
        new = payload["data"]["id"]
        print(f"  created {new}")
        print("  >>> Record this id in APPS above. It cannot be looked up again.")
    return rc


def show():
    rc = 0
    for label, cfg in APPS.items():
        print(f"\n{label}")
        if not cfg["request"]:
            print("  no request recorded — run with --create")
            rc = 1
            continue

        status, payload = call(f"analyticsReportRequests/{cfg['request']}")
        if status != 200:
            print(f"  request {cfg['request']} — HTTP {status}")
            print("  If this is 404 the request was deleted; --create a new one.")
            rc = 1
            continue

        attrs = payload["data"]["attributes"]
        print(f"  request   {cfg['request']}")
        print(f"  access    {attrs.get('accessType')}")

        # Apple stops an ONGOING request that nobody reads, and reports it as a
        # flag on the request rather than an error on the reports — so a run that
        # finds no reports looks identical to one that has been switched off.
        stopped = attrs.get("stoppedDueToInactivity")
        print(f"  stopped   {stopped}"
              + ("   <-- READ THE REPORTS OR RECREATE IT" if stopped else ""))

        status, payload = call(
            f"analyticsReportRequests/{cfg['request']}/reports?limit=200")
        if status != 200:
            print(f"  reports   could not look, HTTP {status}")
            rc = 1
            continue

        reports = payload.get("data", [])
        if not reports:
            print("  reports   none yet — Apple takes about a day for the first set")
            continue

        print(f"  reports   {len(reports)}")
        for r in sorted(reports, key=lambda x: x["attributes"].get("name", "")):
            a = r["attributes"]
            print(f"    {a.get('category','?'):<28} {a.get('name','?')}")
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--create", action="store_true",
                    help="start ONGOING requests for apps with no id recorded")
    args = ap.parse_args()
    return create() if args.create else show()


if __name__ == "__main__":
    sys.exit(main())
