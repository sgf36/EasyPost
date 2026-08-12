"""Publish files from `site/` to the Bluehost cPanel host.

This is a real API client, not a browser workaround. It authenticates with a
cPanel API token, so it needs no logged-in session and no human at a keyboard.

    python packaging/deploy_site.py download.html
    python packaging/deploy_site.py --all
    python packaging/deploy_site.py download.html --dry-run

**The token is never stored in this repository, which is public.** It lives in
the operating system credential store, under service `cpanel-easypost-site`,
account `spencgh6`. Set it once:

    python -c "import keyring; keyring.set_password('cpanel-easypost-site','spencgh6','<token>')"

or export `CPANEL_API_TOKEN`, which takes precedence and is the route CI would
use. Create or revoke tokens in cPanel under Security -> Manage API Tokens.

Every upload is verified by fetching the file back over HTTPS and comparing it
to what was sent, because the File Manager listing shows what is on disk and
not what the web server actually serves. Those can differ, and only the second
one matters.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

try:
    import keyring
except ImportError:  # keyring is in requirements.txt, but do not hard-fail
    keyring = None

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = REPO_ROOT / "site"

HOST = "https://box5192.bluehost.com:2083"
REMOTE_DIR = "/home2/spencgh6/easy-post.spencerfields.com"
CPANEL_USER = "spencgh6"
PUBLIC_BASE = "https://easy-post.spencerfields.com"

KEYRING_SERVICE = "cpanel-easypost-site"


def load_token() -> str:
    token = os.environ.get("CPANEL_API_TOKEN")
    if token:
        return token.strip()
    if keyring is not None:
        token = keyring.get_password(KEYRING_SERVICE, CPANEL_USER)
        if token:
            return token.strip()
    sys.exit(
        "No cPanel API token. Set CPANEL_API_TOKEN, or store it under "
        f"keyring service '{KEYRING_SERVICE}' account '{CPANEL_USER}'. "
        "See this file's docstring."
    )


def uapi(token: str, module: str, function: str, **params):
    response = requests.post(
        f"{HOST}/execute/{module}/{function}",
        headers={"Authorization": f"cpanel {CPANEL_USER}:{token}"},
        data=params,
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    # UAPI answers 200 with status=0 on failure, so the HTTP code alone proves
    # nothing about whether the write happened.
    if not payload.get("status"):
        raise RuntimeError(f"{module}::{function} failed: {payload.get('errors')}")
    return payload.get("data")


def upload(token: str, name: str, dry_run: bool = False) -> bool:
    local = SITE_DIR / name
    if not local.is_file():
        print(f"  MISSING  {name} — not in site/")
        return False

    content = local.read_text(encoding="utf-8")
    if dry_run:
        print(f"  would send  {name}  ({len(content.encode()):,} bytes)")
        return True

    uapi(
        token,
        "Fileman",
        "save_file_content",
        dir=REMOTE_DIR,
        file=name,
        content=content,
        from_charset="UTF-8",
        to_charset="UTF-8",
    )

    # The host's mod_security answers 406 to the default python-requests agent,
    # so the read-back has to look like a browser or it fails on a live file.
    served = requests.get(
        f"{PUBLIC_BASE}/{name}",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=60,
    )
    served.raise_for_status()
    # The host returns `text/html` with no charset, so requests falls back to
    # ISO-8859-1 and every non-ASCII character decodes to mojibake. Comparing
    # that against the real file reports corruption that is not there. The
    # pages declare UTF-8 in a meta tag, so decode as UTF-8 and say so.
    served.encoding = "utf-8"
    ok = _equivalent(content, served.text)
    # save_file_content collapses whitespace between tags. The page renders
    # identically, so compare with that normalised away rather than chasing a
    # byte count that is expected to shrink.
    # Plain ASCII: this runs in a cp1252 console, which cannot encode an arrow.
    print(f"  {'ok      ' if ok else 'MISMATCH'} {name}  "
          f"sent {len(content.encode()):,} -> served {len(served.content):,}")
    return ok


def _equivalent(sent: str, served: str) -> bool:
    # cPanel's save collapses and shifts whitespace between tags, so the served
    # byte count is expected to differ slightly. Compare with all whitespace
    # removed: that still catches a truncated, stale or mis-encoded file, which
    # is what this check exists for.
    return "".join(sent.split()) == "".join(served.split())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="file names inside site/")
    parser.add_argument("--all", action="store_true", help="every file in site/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    names = sorted(p.name for p in SITE_DIR.iterdir() if p.is_file()) if args.all \
        else args.files
    if not names:
        parser.error("name at least one file, or pass --all")

    token = load_token()
    print(f"{PUBLIC_BASE}  ({len(names)} file(s))")
    results = [upload(token, name, args.dry_run) for name in names]

    failed = results.count(False)
    print(f"\n{len(results) - failed} published, {failed} failed"
          f"{' (dry run — nothing sent)' if args.dry_run else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
