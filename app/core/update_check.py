"""Notify direct-download users when a newer version has been released.

The Microsoft Store and the Mac App Store update their apps themselves, so this
only applies to the direct-download builds — the ``.zip`` and the ``.dmg`` a
user unpacks by hand, which have no auto-update channel. On launch the app asks
the GitHub Releases API for the latest tag and, if it is newer than the running
:data:`app.config.APP_VERSION`, the main window shows a dismissable banner.

Everything here is best-effort and fail-silent: a rate-limited API, no network,
or an unexpected payload simply yields "no update known" rather than an error —
a version check must never get between the user and their app.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional, Tuple

from app.config import APP_VERSION, MAS_BUILD, STORE_BUILD

# The repository's "latest published release" endpoint. Returns the newest
# non-prerelease, non-draft release — exactly what a user should be nudged to.
RELEASES_API = "https://api.github.com/repos/sgf36/EasyPost/releases/latest"

# Where the banner sends the user — the download page, not a raw asset, so they
# read the SmartScreen/checksum guidance and pick the right platform.
RELEASES_PAGE = "https://easy-post.spencerfields.com/download.html"


def update_check_supported() -> bool:
    """True only for direct-download builds. The Store and Mac App Store builds
    update through their store, so nudging them to a GitHub download would be
    wrong (and, on the sandboxed MAS build, impossible)."""
    return not (STORE_BUILD or MAS_BUILD)


def _parse_version(text: str) -> Tuple[int, ...]:
    """Turn ``"v1.0.8"`` / ``"1.0.8"`` into ``(1, 0, 8)`` for ordering.

    Tolerant by design: a leading ``v`` is dropped, each dotted part is read up
    to its first non-digit, and anything unparseable in a part becomes 0. A
    malformed tag therefore compares low rather than raising.
    """
    cleaned = text.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def latest_release_tag(timeout: float = 6.0) -> Optional[str]:
    """The latest published release's tag (e.g. ``"v1.0.8"``), or None if the
    API could not be reached or returned nothing usable. Never raises."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "EasyPostDesktop-update-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except Exception:
        return None
    tag = payload.get("tag_name")
    return tag if isinstance(tag, str) and tag else None


def check_for_update() -> Optional[str]:
    """Return the latest release tag if it is newer than the running build, or
    None (no update, unsupported build, or the check could not complete).

    Safe to call from a worker thread; it does its own network I/O and swallows
    every failure.
    """
    if not update_check_supported():
        return None
    tag = latest_release_tag()
    if not tag:
        return None
    try:
        if _parse_version(tag) > _parse_version(APP_VERSION):
            return tag
    except Exception:
        return None
    return None
