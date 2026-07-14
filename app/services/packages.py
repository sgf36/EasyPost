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

DEFAULT_CARRIERS = ("usps", "fedex", "ups", "dhlexpress")


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


def _cache_predefined_packages(carriers: tuple, packages: list[PredefinedPackage]) -> None:
    with db_cursor() as cur:
        cur.execute(
            f"DELETE FROM predefined_packages_cache WHERE carrier IN ({','.join('?' * len(carriers))})",
            carriers,
        )
        for p in packages:
            cur.execute(
                """
                INSERT INTO predefined_packages_cache (carrier, name, description, dimensions, max_weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                (p.carrier, p.name, p.description, p.dimensions, p.max_weight),
            )


def _cached_predefined_packages(carriers: tuple) -> list[PredefinedPackage]:
    with db_cursor() as cur:
        cur.execute(
            f"SELECT * FROM predefined_packages_cache "
            f"WHERE carrier IN ({','.join('?' * len(carriers))}) ORDER BY carrier, name",
            carriers,
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


def list_predefined_packages(carriers: tuple = DEFAULT_CARRIERS) -> list[PredefinedPackage]:
    """Fetches carrier predefined packages live from EasyPost; on any
    failure, falls back to whatever was cached from a previous successful
    fetch (possibly empty on first run with no network).
    """
    try:
        client = client_manager.get_client()
        result = client.carrier_metadata.retrieve(carriers=list(carriers), types=["predefined_packages"])
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
        return _cached_predefined_packages(carriers)

    if packages:
        _cache_predefined_packages(carriers, packages)
    return packages
