"""HTS (Harmonized Tariff Schedule) code lookup for customs declarations.

EasyPost has no HTS lookup of its own — CustomsItem.hts_number is just a
free-text field the caller supplies. The U.S. International Trade
Commission runs the actual source of truth: a free, unauthenticated REST
search at hts.usitc.gov. Every search queries it live so results are
always current; successful results are also cached locally so repeat
searches are instant and still work if USITC's API is unreachable (its
rate limits are unpublished and its response shape has changed before
without notice, so failures here are expected occasionally, not
exceptional).
"""

import logging
from dataclasses import dataclass

import requests

from app.core.db import db_cursor

logger = logging.getLogger(__name__)

USITC_SEARCH_URL = "https://hts.usitc.gov/reststop/search"
REQUEST_TIMEOUT_SECONDS = 10
CACHE_RESULT_LIMIT = 100


@dataclass
class HtsCodeResult:
    htsno: str
    description: str
    general_rate: str
    special_rate: str
    other_rate: str
    units: str
    indent: int
    from_cache: bool = False


def _coerce_units(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return str(value) if value else ""


def _coerce_indent(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_row(row: dict) -> HtsCodeResult:
    return HtsCodeResult(
        htsno=row.get("htsno") or "",
        description=row.get("description") or "",
        general_rate=row.get("general") or "",
        special_rate=row.get("special") or "",
        other_rate=row.get("other") or "",
        units=_coerce_units(row.get("units")),
        indent=_coerce_indent(row.get("indent")),
    )


def _cache_results(results: list[HtsCodeResult]) -> None:
    with db_cursor() as cur:
        for r in results:
            cur.execute(
                """
                INSERT INTO hts_cache (
                    htsno, description, general_rate, special_rate,
                    other_rate, units, indent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (r.htsno, r.description, r.general_rate, r.special_rate, r.other_rate, r.units, r.indent),
            )


def _search_cache(keyword: str) -> list[HtsCodeResult]:
    like = f"%{keyword}%"
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT htsno, description, general_rate, special_rate,
                   other_rate, units, indent
            FROM hts_cache
            WHERE htsno LIKE ? OR description LIKE ?
            ORDER BY cached_at DESC
            LIMIT ?
            """,
            (like, like, CACHE_RESULT_LIMIT),
        )
        rows = cur.fetchall()
    return [
        HtsCodeResult(
            htsno=row["htsno"] or "",
            description=row["description"] or "",
            general_rate=row["general_rate"] or "",
            special_rate=row["special_rate"] or "",
            other_rate=row["other_rate"] or "",
            units=row["units"] or "",
            indent=row["indent"] or 0,
            from_cache=True,
        )
        for row in rows
    ]


def search_hts_codes(keyword: str) -> list[HtsCodeResult]:
    """Searches live against USITC; on any failure (network, timeout, non-200,
    unexpected response shape), falls back to previously-cached results
    matching the keyword. Returned results have `from_cache=True` set only
    in that fallback case, so the UI can show an "offline" indicator. An
    empty live result set (a legitimate "no match") is returned as-is,
    never silently replaced with unrelated cached data.
    """
    keyword = keyword.strip()
    if not keyword:
        return []

    try:
        response = requests.get(
            USITC_SEARCH_URL, params={"keyword": keyword}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected USITC response shape: {type(data)}")
        results = [_parse_row(row) for row in data]
    except Exception:
        logger.exception("Live USITC search failed for %r; falling back to cache", keyword)
        return _search_cache(keyword)

    if results:
        _cache_results(results)
    return results
