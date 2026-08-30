"""Build App Analytics campaign links for both Easy-Post apps from the registry.

    python packaging/campaign_links.py            # print the links
    python packaging/campaign_links.py --check    # validate only, exit 1 on a fault

Why this exists rather than a list of links in a document.

**Apple matches campaign tokens literally.** `site-easypost-download` and
`Site-EasyPost-Download` are two rows under Acquisition > Campaigns that Apple
will never merge, and a single typo silently mints a third. Hand-typed tokens
across a site published in forty-odd languages fragment into data that looks
fine until you try to read it, and by then the traffic that produced it is
months gone. So the tokens live in `campaigns.json` and the links are generated.
Nobody types a token.

## Two things about this that are not guessable

**`pt` is required, and a link without it is silently useless.** It still opens
the App Store and installs still happen; none of it is attributed, and that
cannot be recovered later.

**The provider token is per ACCOUNT, not per app.** Apple minted `129201947` on
Wren's first campaign and returned the same value for both Easy-Post apps
afterwards. So a new app or a new campaign needs no App Store Connect visit at
all -- add it to the registry and generate. Verified 2026-08-30 by comparing the
links App Store Connect returned for all three apps.

**There is no API.** `analyticsCampaigns`, `campaigns` and the app-scoped
variants all answer 404 "not a defined resource type" under the submission key,
while a known-good path answers 200 and a real-but-unavailable one answers 406.
A missing path and a forbidden one answer differently, so the 404 is the API's
shape rather than a permissions gap.

## The sibling, and the duplication

The wren repository carries its own `store/campaign_links.py` for Wren alone.
This one covers two apps because two ship from this repository. They are
deliberately separate copies rather than shared tooling: each repository stays
self-contained and deployable on its own, and the thing that must not drift is
the *registry*, not the eighty lines that read it. If a third repository ever
needs one, that is the point to move all three into `sgf36/site-tooling`
alongside the i18n deployer.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REGISTRY = HERE / "campaigns.json"

CT_LIMIT = 30
TOKEN_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf8"))


def problems(data: dict) -> list[str]:
    """Everything wrong with the registry, rather than the first thing."""
    out: list[str] = []

    if not data.get("providerToken"):
        out.append("providerToken is missing. It is 129201947 for this account; "
                   "see the module docstring before assuming it needs minting.")
    if not data.get("apps"):
        out.append("no apps in the registry")

    for a, app in enumerate(data.get("apps", [])):
        label = app.get("name") or f"apps[{a}]"
        if not app.get("appId"):
            out.append(f"{label}: appId is missing")

        # Scoped per app, not globally: App Analytics reports each app
        # separately, so the same token under two apps is fine and often
        # right -- `site-software-apps-page` means the same thing for each.
        seen: dict[str, int] = {}
        campaigns = app.get("campaigns", [])
        if not campaigns:
            out.append(f"{label}: no campaigns")

        for i, c in enumerate(campaigns):
            tok = c.get("token", "")
            where = f"{label} campaigns[{i}]"

            if not tok:
                out.append(f"{where}: no token")
                continue
            if len(tok) > CT_LIMIT:
                out.append(f"{where} {tok!r}: {len(tok)} characters, limit is {CT_LIMIT}")
            if not TOKEN_RE.match(tok):
                out.append(
                    f"{where} {tok!r}: must be lowercase letters, digits and single "
                    f"hyphens. Apple allows more, but case and separator drift is how "
                    f"one campaign becomes three rows.")
            if tok in seen:
                out.append(f"{where} {tok!r}: already used by campaigns[{seen[tok]}] "
                           f"of the same app")
            else:
                seen[tok] = i

            for field in ("channel", "surface", "creative"):
                if not c.get(field):
                    out.append(f"{where} {tok!r}: {field} is empty")

    return out


def links(data: dict) -> list[tuple[str, str, str]]:
    """(app name, token, url) for every campaign in the registry."""
    pt = data["providerToken"]
    return [
        (app["name"], c["token"],
         f"https://apps.apple.com/app/apple-store/id{app['appId']}"
         f"?pt={pt}&ct={c['token']}&mt=8")
        for app in data["apps"]
        for c in app["campaigns"]
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="validate only; print nothing on success")
    args = ap.parse_args()

    data = load()
    faults = problems(data)
    if faults:
        print("registry is not usable:", file=sys.stderr)
        for f in faults:
            print(f"  {f}", file=sys.stderr)
        return 1

    if args.check:
        return 0

    rows = links(data)
    width = max(len(t) for _, t, _ in rows)
    current = None
    for name, tok, url in rows:
        if name != current:
            print(f"\n{name}")
            current = name
        print(f"  {tok.ljust(width)}  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
