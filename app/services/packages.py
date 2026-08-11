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

from app.core.client import client_manager
from app.core.db import db_cursor

logger = logging.getLogger(__name__)

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


def _coerce_dimensions(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return str(value) if value else ""


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
        client = client_manager.get_client()
        result = client.carrier_metadata.retrieve(types=["predefined_packages"])
        packages = [
            PredefinedPackage(
                carrier=pkg["carrier"],
                name=pkg["name"],
                description=pkg.get("description"),
                dimensions=_coerce_dimensions(pkg.get("dimensions")),
                max_weight=pkg.get("max_weight"),
            )
            for carrier_entry in result
            for pkg in (carrier_entry.get("predefined_packages") or [])
        ]
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
    anything (a first run with no network)."""
    seen = {p.name for p in list_predefined_packages() if p.name}
    return sorted(seen, key=str.casefold)
