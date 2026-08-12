"""Saved package presets: user-defined custom dimensions, and carrier
predefined packages (USPS flat rate boxes, FedEx envelopes, etc.) sourced
live from EasyPost's Carrier Metadata endpoint.

Predefined packages are cached locally so the Create Shipment page still
has something to show if that live call fails — the same
live-first/cache-fallback pattern as app/services/hts_lookup.py. Unlike
that cache, this one is fully replaced on every successful refresh (not
accumulated), since each refresh fetches the complete list per carrier
rather than one keyword search at a time.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.db import db_cursor
from app.services.carriers import (
    carrier_display_name,
    coerce_dimensions,
    retrieve_carrier_metadata,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PACKAGE_CHOICE_SEPARATOR",
    "PredefinedPackage",
    "SavedPackage",
    "carrier_display_name",
    "delete_saved_package",
    "list_predefined_packages",
    "list_saved_packages",
    "package_code_from_choice",
    "predefined_package_choices",
    "predefined_package_names",
    "save_package",
]

# Predefined packages are fetched for EVERY carrier EasyPost knows about, not a
# curated subset: the useful set depends on which carriers a given user has
# enabled (Royal Mail in the UK, Australia Post in AU, ...), which this app has
# no business second-guessing. EasyPost's carrier-metadata endpoint returns all
# carriers when the `carriers` filter is omitted — see list_predefined_packages.


@dataclass
class SavedPackage:
    id: int
    name: str
    length: Optional[float]
    width: Optional[float]
    height: Optional[float]
    weight: float


@dataclass
class PredefinedPackage:
    carrier: str
    name: str
    description: Optional[str]
    dimensions: str
    max_weight: Optional[float]


def list_saved_packages() -> list[SavedPackage]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM saved_packages ORDER BY name")
        rows = cur.fetchall()
    return [
        SavedPackage(
            id=row["id"],
            name=row["name"],
            length=row["length"],
            width=row["width"],
            height=row["height"],
            weight=row["weight"],
        )
        for row in rows
    ]


def save_package(name: str, length: float, width: float, height: float, weight: float) -> None:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO saved_packages (name, length, width, height, weight) VALUES (?, ?, ?, ?, ?)",
            (name, length, width, height, weight),
        )


def delete_saved_package(package_id: int) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM saved_packages WHERE id = ?", (package_id,))


def _cache_predefined_packages(packages: list[PredefinedPackage]) -> None:
    """Replace the whole predefined-package cache with a fresh full fetch."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM predefined_packages_cache")
        for p in packages:
            cur.execute(
                """
                INSERT INTO predefined_packages_cache (carrier, name, description, dimensions, max_weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                (p.carrier, p.name, p.description, p.dimensions, p.max_weight),
            )


def _cached_predefined_packages() -> list[PredefinedPackage]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM predefined_packages_cache ORDER BY carrier, name"
        )
        rows = cur.fetchall()
    return [
        PredefinedPackage(
            carrier=row["carrier"],
            name=row["name"],
            description=row["description"],
            dimensions=row["dimensions"] or "",
            max_weight=row["max_weight"],
        )
        for row in rows
    ]


def list_predefined_packages() -> list[PredefinedPackage]:
    """Fetch predefined packages for EVERY carrier EasyPost supports, live.

    The endpoint is called with no ``carriers`` filter, so the list reflects the
    whole platform rather than one account's enabled carriers — a user who has
    Royal Mail, Australia Post, or any other carrier sees its packages without
    the app hard-coding a set. On any failure it falls back to whatever the last
    successful fetch cached (possibly empty on a first run with no network).
    """
    try:
        result = retrieve_carrier_metadata(types=["predefined_packages"])
        packages = [
            PredefinedPackage(
                # The enclosing entry's `name` is the authoritative carrier code;
                # the package's own `carrier` field agrees with it, but reading
                # the parent means one less field to be wrong.
                carrier=carrier_entry.get("name") or pkg.get("carrier") or "",
                name=pkg.get("name") or "",
                description=pkg.get("description"),
                dimensions=coerce_dimensions(pkg.get("dimensions")),
                max_weight=pkg.get("max_weight"),
            )
            for carrier_entry in result
            for pkg in (carrier_entry.get("predefined_packages") or [])
        ]
        packages = [p for p in packages if p.carrier and p.name]
    except Exception:
        logger.exception("Live carrier predefined-package fetch failed; falling back to cache")
        return _cached_predefined_packages()

    if packages:
        _cache_predefined_packages(packages)
    return packages


def predefined_package_names() -> list[str]:
    """The distinct carrier package codes, sorted, for a dropdown of choices.

    EasyPost's ``predefined_package`` parcel field takes the bare package code
    (e.g. "FlatRateEnvelope"), interpreted per carrier at rating time, so the
    same name can appear under several carriers — de-duplicated here. Sourced
    through :func:`list_predefined_packages`, so it inherits its
    live-first/cache-fallback behaviour and is simply empty when neither has
    anything (a first run with no network).

    Prefer :func:`predefined_package_choices` for anything shown to a user: a
    bare code is ambiguous when carriers collide (USPS "Letter" vs Royal Mail
    "LETTER"). This name-only form is kept for callers that only need the codes.
    """
    seen = {p.name for p in list_predefined_packages() if p.name}
    return sorted(seen, key=str.casefold)


# Separator between the carrier label and the package code in a dropdown choice,
# e.g. "Royal Mail — LETTER". Chosen because neither EasyPost carrier names nor
# package codes contain " — " (space, em dash, space), so it round-trips: the
# bare code is recovered unambiguously on import by splitting on it.
PACKAGE_CHOICE_SEPARATOR = " — "


def predefined_package_choices() -> list[str]:
    """Carrier-qualified package labels for the batch template dropdown.

    Each entry is ``"<carrier> — <code>"`` (e.g. "Royal Mail — LETTER"), so
    codes that collide across carriers stay distinguishable — the flat Excel
    data-validation list has no carrier heading of its own the way the Create
    Shipment combo does. De-duplicated (two Royal Mail carrier codes that both
    expose "LETTER" collapse to one choice) and sorted by carrier then code.
    Recover the bare code EasyPost wants with :func:`package_code_from_choice`.
    """
    seen: set[str] = set()
    choices: list[str] = []
    for p in list_predefined_packages():
        if not p.name:
            continue
        label = f"{carrier_display_name(p.carrier)}{PACKAGE_CHOICE_SEPARATOR}{p.name}"
        if label not in seen:
            seen.add(label)
            choices.append(label)
    return sorted(choices, key=str.casefold)


def package_code_from_choice(choice: str) -> str:
    """Recover the bare EasyPost package code from a batch dropdown choice.

    Accepts either a carrier-qualified label ("Royal Mail — LETTER") or a bare
    code typed directly into a CSV ("LETTER"), and returns the code that
    EasyPost's ``predefined_package`` field expects. Splitting on the *last*
    separator keeps a carrier label that itself contained one safe.
    """
    if PACKAGE_CHOICE_SEPARATOR in choice:
        return choice.rsplit(PACKAGE_CHOICE_SEPARATOR, 1)[1].strip()
    return choice.strip()
