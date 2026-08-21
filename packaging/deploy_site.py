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

Every upload is verified by reading the file back and comparing it to what was
sent. Static files are read back over HTTPS, because the File Manager listing
shows what is on disk and not what the web server actually serves; those can
differ, and only the second one matters. Files the server *executes* are read
back over the API instead — see `_verify_over_api`.
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

# Suffixes the web server runs instead of serving verbatim. A GET on one of
# these returns the script's output, never its source, so the public URL cannot
# verify it. Anything not listed here is assumed to be served as written.
BINARY_SUFFIXES = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".pdf"}
EXECUTED_SUFFIXES = {".php", ".php5", ".php7", ".php8", ".phtml", ".cgi", ".pl"}


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

    # Binary assets cannot go through save_file_content: that endpoint takes a
    # text body and performs a charset conversion, which mangles image bytes.
    # They go up as a multipart upload instead and are verified byte-for-byte.
    if Path(name).suffix.lower() in BINARY_SUFFIXES:
        return _upload_binary(token, name, local, dry_run)

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

    if Path(name).suffix.lower() in EXECUTED_SUFFIXES:
        ok, read_back = _verify_over_api(token, name, content)
    else:
        ok, read_back = _verify_over_https(name, content)

    # save_file_content collapses whitespace between tags. The page renders
    # identically, so compare with that normalised away rather than chasing a
    # byte count that is expected to shrink.
    # Plain ASCII: this runs in a cp1252 console, which cannot encode an arrow.
    print(f"  {'ok      ' if ok else 'MISMATCH'} {name}  "
          f"sent {len(content.encode()):,} -> {read_back}")
    return ok


def _upload_binary(token: str, name: str, local: Path, dry_run: bool) -> bool:
    """Send an image or font as bytes, then compare the served bytes exactly.

    save_file_content is a text API. Handing it a PNG produces a file that is
    the right length and the wrong content, and the failure only shows up as a
    broken image in a browser, so this path never touches it.
    """
    raw = local.read_bytes()
    if dry_run:
        print(f"  would send  {name}  ({len(raw):,} bytes, binary)")
        return True

    with local.open("rb") as fh:
        response = requests.post(
            f"{HOST}/execute/Fileman/upload_files",
            headers={"Authorization": f"cpanel {CPANEL_USER}:{token}"},
            data={"dir": REMOTE_DIR, "overwrite": 1},
            files={"file-1": (name, fh, "application/octet-stream")},
            timeout=120,
        )
    response.raise_for_status()
    payload = response.json()
    # upload_files reports per-file success separately from the envelope.
    for entry in (payload.get("data", {}) or {}).get("uploads", []) or []:
        if not entry.get("status"):
            print(f"  REJECTED {name} — {entry.get('reason')}")
            return False

    served = requests.get(
        f"{PUBLIC_BASE}/{name}",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=60,
    )
    served.raise_for_status()
    ok = served.content == raw
    print(f"  {'ok      ' if ok else 'MISMATCH'} {name}  "
          f"sent {len(raw):,} -> served {len(served.content):,} (binary)")
    return ok


def _verify_over_https(name: str, content: str) -> tuple[bool, str]:
    """Read a static file back from the public URL, which is what visitors get."""
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
    return _equivalent(content, served.text), f"served {len(served.content):,}"


def _verify_over_api(token: str, name: str, content: str) -> tuple[bool, str]:
    """Read an executed file back through the API that wrote it.

    The public URL cannot check these. The server runs the file rather than
    serving it, so the response is whatever the script decided to emit, and a
    good deployment still fails the comparison: `contact.php` 302s to the home
    page on any request that is not a POST, and the host answered 409 outright
    to the read-back after its first deploy. Both describe the running script,
    not the bytes that were written, which are what this check is about.
    """
    stored = uapi(
        token,
        "Fileman",
        "get_file_content",
        dir=REMOTE_DIR,
        file=name,
        from_charset="UTF-8",
        to_charset="UTF-8",
    )["content"]
    return _equivalent(content, stored), f"stored {len(stored.encode()):,}"


def _equivalent(sent: str, remote: str) -> bool:
    # cPanel's save collapses and shifts whitespace between tags, and normalises
    # CRLF to LF, so the read-back is expected to differ slightly either way it
    # is fetched. Compare with all whitespace removed: that still catches a
    # truncated, stale or mis-encoded file, which is what this check exists for.
    return "".join(sent.split()) == "".join(remote.split())


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
