"""Store ratings across all three storefronts, in one line each.

    python tools/ratings_watch.py

Why this exists: no platform tells you whether an in-app review prompt produced a
rating. Apple's `SKStoreReviewController` reports nothing at all. So the only
honest measurement of the review work described in REVIEW-PROMPT-BRIEF.md is the
store-side count, watched over time. Run it weekly and keep the numbers.

Microsoft needs no authentication — the display catalogue is public. Apple uses
the same App Store Connect key as `asc_analytics.py`.
"""
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

APPLE = {
    "Mac App Store - Easy-Post Desktop": "6797912453",
    "iPhone App Store - Mobile Companion": "6798723985",
}
MS_STORE_ID = "9NDSDL5LV5B5"
BASE = "https://api.appstoreconnect.apple.com/v1"


def token():
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER, "iat": now - 60, "exp": now + 1140,
         "aud": "appstoreconnect-v1"},
        KEY.read_text(), algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"})


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            return e.code, {}
    except Exception as e:                       # network, DNS, TLS
        return 0, {"error": str(e)}


def apple():
    for label, app in APPLE.items():
        # territory=USA keeps it to one storefront; without it the totals are
        # summed across every territory and move for reasons unrelated to us.
        status, payload = get(
            f"{BASE}/apps/{app}/customerReviews?limit=200",
            {"Authorization": f"Bearer {token()}"})
        if status != 200:
            msg = ""
            if isinstance(payload, dict) and payload.get("errors"):
                msg = f" — {payload['errors'][0].get('title','')}"
            print(f"  {label:<36} HTTP {status}{msg}")
            continue
        reviews = payload.get("data", [])
        if not reviews:
            print(f"  {label:<36} 0 reviews")
            continue
        stars = [r["attributes"].get("rating") for r in reviews
                 if r["attributes"].get("rating") is not None]
        avg = sum(stars) / len(stars) if stars else 0
        print(f"  {label:<36} {len(reviews)} reviews, average {avg:.1f}")


def microsoft():
    status, payload = get(
        "https://displaycatalog.mp.microsoft.com/v7.0/products/"
        f"{MS_STORE_ID}?languages=en-US&market=US")
    label = "Microsoft Store - Easy-Post Desktop"
    if status != 200:
        print(f"  {label:<36} HTTP {status}")
        return
    try:
        usage = payload["Product"]["MarketProperties"][0]["UsageData"]
        # UsageData carries several windows (All-time, 30-day…). All-time first.
        row = usage[0]
        print(f"  {label:<36} {row.get('RatingCount', 0)} ratings, "
              f"average {row.get('AverageRating', 0):.1f}")
    except (KeyError, IndexError, TypeError):
        print(f"  {label:<36} unexpected response shape")


def main():
    print("\nStore ratings - " + time.strftime("%Y-%m-%d"))
    apple()
    microsoft()
    print("\n  Zero everywhere is the starting point, not a fault. See "
          "REVIEW-PROMPT-BRIEF.md.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
