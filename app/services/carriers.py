"""Carrier reference data from EasyPost's Carrier Metadata endpoint: the
carriers themselves, and the service levels each one offers.

Why this module exists at all: a batch shipment is never rated by EasyPost, so
the carrier and service have to be declared up front, by name, at create time
(see app/services/batches.py). Names typed by hand are names typed wrong, and a
wrong one fails silently — the batch still reaches `created`, and only the
purchase reports "<carrier> does not offer service <service> for this shipment".
So the app needs the real catalogue.

Two API facts worth stating plainly, because both are easy to get backwards:

* The metadata endpoint returns carrier codes in **lowercase** ("royalmailv3"),
  while ``rate.carrier`` returns CamelCase ("RoyalMailV3") and
  ``carrier_account.type`` returns "RoyalMailV3Account". These are three
  spellings of one thing. Verified against the live API: the shipment-level
  ``carrier`` field accepts either case and purchases identically, so codes are
  passed through exactly as the metadata endpoint returns them and no case
  translation happens anywhere.
* ``carrier_account.all()`` is production-only — a test key gets a
  ForbiddenError. Anything that depends on knowing which carriers an account has
  enabled therefore has to degrade gracefully rather than fail.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceLevel:
    """One purchasable service, e.g. Royal Mail's "RoyalMail2ndClassSignedFor".

    ``name`` is the exact string the shipment ``service`` field takes; ``carrier``
    is the exact string its ``carrier`` field takes. Both are reproduced verbatim
    from the API rather than re-cased, so a value that round-trips through this
    catalogue is a value EasyPost accepts.
    """

    carrier: str
    name: str
    human_readable: Optional[str] = None
    dimensions: str = ""
    max_weight: Optional[float] = None

    @property
    def display_name(self) -> str:
        """The service's own label. EasyPost frequently just echoes the code back
        as ``human_readable``, so this is often the code itself — callers that
        want it prettified should humanise it for display."""
        return self.human_readable or self.name

    @property
    def requires_signature(self) -> bool:
        """Whether this service can only be bought with the signature option set.

        Verified empirically against the live API, and it is a genuine trap: a
        batch declaring "RoyalMail2ndClassSignedFor" *without*
        ``options.delivery_confirmation = "SIGNATURE"`` reaches state `created`
        quite happily and then fails at purchase with "RoyalMailV3 does not offer
        service RoyalMail2ndClassSignedFor for this shipment". Adding the option
        and changing nothing else purchases successfully. EasyPost does not
        document this (the delivery_confirmation option is documented only for
        FedEx, USPS, Canada Post, GSO and DHL Express), so this is an observed
        behaviour rather than a contract — it is a name-shape heuristic, used to
        set the option automatically, never to block a purchase.
        """
        haystack = f"{self.name} {self.human_readable or ''}".casefold()
        return "signed" in haystack or "signature" in haystack


@dataclass(frozen=True)
class CarrierAccountRef:
    """An enabled carrier account, as reported by a production API key."""

    id: str
    type: str
    readable: Optional[str]

    @property
    def carrier_code(self) -> str:
        """The account type reduced towards a metadata carrier code, e.g.
        "RoyalMailV3Account" -> "royalmailv3". Not a perfect mapping — see
        :func:`enabled_carrier_codes`."""
        base = self.type[: -len("Account")] if self.type.endswith("Account") else self.type
        return base.casefold()


# Display-name overrides, keyed by the LOWERCASE code the API actually returns.
#
# The previous version of this map was keyed CamelCase ("RoyalMailV3"), which
# meant every single lookup missed and every carrier fell through to its raw
# code. Keyed correctly, the map is only needed for the handful of carriers
# whose own `human_readable` is just the CamelCase code echoed back rather than
# a real name; everything else is better served by the API's own label, so the
# map is deliberately kept short instead of restating 96 carriers.
_CARRIER_DISPLAY_OVERRIDES = {
    "royalmailv3": "Royal Mail V3",
    "epostglobalv2": "ePostGlobal V2",
    "ontracv3": "OnTrac V3",
    "osmworldwidev2": "OSM Worldwide V2",
    "nextdayexpress": "NextDay Express",
    "cslogistics": "CS Logistics",
    "sda": "SDA",
    "upsdap": "UPS DAP",
}


def coerce_dimensions(value) -> str:
    """The metadata endpoint returns `dimensions` as a list of strings (often
    empty). Flatten to one display string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return str(value) if value else ""


# ---------------------------------------------------------------------------
# Carrier names
# ---------------------------------------------------------------------------

# Memo of the carriers_cache table. Reset whenever the cache is rewritten, so a
# refreshed catalogue takes effect without restarting the app.
_carrier_names: Optional[dict[str, str]] = None


def _cached_carrier_names() -> dict[str, str]:
    with db_cursor() as cur:
        cur.execute("SELECT name, human_readable FROM carriers_cache")
        return {row["name"]: row["human_readable"] for row in cur.fetchall()}


def _carrier_name_map() -> dict[str, str]:
    global _carrier_names
    if _carrier_names is None:
        _carrier_names = _cached_carrier_names()
    return _carrier_names


# Labels for codes derived from carrier ACCOUNT types rather than the metadata
# endpoint ("hermes" from HermesAccount, where the carrier is published as
# "evri"). Deliberately held apart from carriers_cache and never persisted.
#
# They were briefly written into carriers_cache, which broke the reverse
# label -> code lookup: "Evri" then resolved to the account code "hermes"
# instead of the carrier "evri", and Evri, DHL Express and DHL eCommerce all
# silently vanished from the picker. Display and identity are different jobs;
# only the metadata endpoint is authoritative about which carriers exist.
_account_labels: dict[str, str] = {}


def _record_carrier_names(pairs: list[tuple[str, Optional[str]]]) -> None:
    """Replace the carrier-name cache with what the metadata endpoint reports.

    A full replace, not an upsert: the endpoint is always called unfiltered, so
    its answer is the complete set of carriers. Replacing also evicts any stale
    or wrongly-recorded row rather than letting it linger forever, which matters
    because a bad row here removes a carrier from the picker.
    """
    global _carrier_names
    if not pairs:
        return
    with db_cursor() as cur:
        cur.execute("DELETE FROM carriers_cache")
        for name, human_readable in pairs:
            cur.execute(
                "INSERT INTO carriers_cache (name, human_readable) VALUES (?, ?)",
                (name, human_readable),
            )
    _carrier_names = None


def carrier_display_name(carrier: str, human_readable: Optional[str] = None) -> str:
    """A human-readable label for an EasyPost carrier code.

    Resolution order: an explicit override, then the label supplied by the caller
    or previously cached from the API, then the raw code. Lookup is case
    -insensitive so a CamelCase code from ``rate.carrier`` resolves to the same
    label as the lowercase one from the metadata endpoint.

    Note that "royalmail" and "royalmailv3" are deliberately labelled distinctly
    rather than both collapsing to "Royal Mail": they are separate carriers with
    different service catalogues (28 services versus 243), and picking the wrong
    one fails at purchase time. EasyPost's own account listing calls the latter
    "Royal Mail V3" too.
    """
    if not carrier:
        return ""
    key = carrier.casefold()
    override = _CARRIER_DISPLAY_OVERRIDES.get(key)
    if override:
        return override
    if human_readable:
        return human_readable
    # Metadata first (authoritative), then a label learnt from a carrier
    # account, then the raw code. The account labels are display-only — see
    # _account_labels — so an account type can never masquerade as a carrier.
    return _carrier_name_map().get(key) or _account_labels.get(key) or carrier


# ---------------------------------------------------------------------------
# Metadata retrieval
# ---------------------------------------------------------------------------


def retrieve_carrier_metadata(types: list[str]):
    """Call the Carrier Metadata endpoint and record every carrier name it
    reports, so :func:`carrier_display_name` improves as a side effect of any
    catalogue refresh. Raises on failure — callers own the fallback policy.

    No ``carriers`` filter is sent: the list reflects every carrier EasyPost
    supports rather than a hard-coded subset, so a user with Royal Mail or
    Australia Post enabled sees their services without this app second-guessing
    which carriers matter.
    """
    client = client_manager.get_client()
    result = client.carrier_metadata.retrieve(types=types)
    _record_carrier_names(
        [(e.get("name"), e.get("human_readable")) for e in result if e.get("name")]
    )
    return result


# ---------------------------------------------------------------------------
# Service levels
# ---------------------------------------------------------------------------


def _cache_service_levels(levels: list[ServiceLevel]) -> None:
    """Replace the whole service-level cache with a fresh full fetch — the same
    replace-on-refresh policy as predefined_packages_cache, since each refresh
    fetches the complete catalogue rather than one query's worth."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM service_levels_cache")
        for s in levels:
            cur.execute(
                """
                INSERT INTO service_levels_cache
                    (carrier, name, human_readable, dimensions, max_weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                (s.carrier, s.name, s.human_readable, s.dimensions, s.max_weight),
            )


def _cached_service_levels() -> list[ServiceLevel]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM service_levels_cache ORDER BY carrier, name")
        rows = cur.fetchall()
    return [
        ServiceLevel(
            carrier=row["carrier"],
            name=row["name"],
            human_readable=row["human_readable"],
            dimensions=row["dimensions"] or "",
            max_weight=row["max_weight"],
        )
        for row in rows
    ]


def list_service_levels() -> list[ServiceLevel]:
    """Every service level EasyPost knows about, live, falling back to the last
    successful fetch on any failure (possibly empty on a first run offline)."""
    try:
        result = retrieve_carrier_metadata(types=["service_levels"])
        levels = [
            ServiceLevel(
                carrier=entry.get("name") or svc.get("carrier") or "",
                name=svc.get("name") or "",
                human_readable=svc.get("human_readable"),
                dimensions=coerce_dimensions(svc.get("dimensions")),
                max_weight=svc.get("max_weight"),
            )
            for entry in result
            for svc in (entry.get("service_levels") or [])
        ]
        levels = [s for s in levels if s.carrier and s.name]
    except Exception:
        logger.exception("Live service-level fetch failed; falling back to cache")
        return _cached_service_levels()

    if levels:
        _cache_service_levels(levels)
    return levels


# ---------------------------------------------------------------------------
# Which carriers this account can actually use
# ---------------------------------------------------------------------------


def list_carrier_accounts() -> Optional[list[CarrierAccountRef]]:
    """The account's enabled carrier accounts, or None when that cannot be
    determined — which is the normal case in test mode, where the endpoint
    answers with a ForbiddenError. None means "unknown", never "none enabled",
    and callers must not treat the two alike."""
    try:
        accounts = client_manager.get_client().carrier_account.all()
    except Exception:
        logger.debug("Carrier accounts unavailable (expected on a test key)", exc_info=True)
        return None

    refs = [
        CarrierAccountRef(
            id=a.get("id") or "", type=a.get("type") or "", readable=a.get("readable")
        )
        for a in accounts
        if a.get("type")
    ]

    # Learn the account-derived codes for DISPLAY only. Several account types
    # reduce to a code the metadata endpoint has never heard of — verified
    # against the real production account, "HermesAccount" gives "hermes" where
    # the carrier is published as "evri", and likewise dhlecs,
    # dhlexpressdefault and fedexdefault. Without this the UI falls back to the
    # raw code and the user reads "hermes" where EasyPost itself says "Evri".
    # Kept out of carriers_cache: putting them there made them compete with real
    # carriers in the reverse lookup and dropped three carriers from the picker.
    _account_labels.update({r.carrier_code: r.readable for r in refs if r.readable})
    return refs


def enabled_carrier_codes() -> Optional[set[str]]:
    """Lowercase metadata carrier codes for the enabled accounts, or None if
    unknown.

    Matching is done two ways because neither alone is sufficient. Account types
    do not map cleanly onto metadata codes: verified against a real production
    account, "HermesAccount" would have to match the carrier "evri",
    "DhlEcsAccount" would have to match "dhlecommercesolutions", and
    "DhlExpressDefaultAccount" would have to match "dhlexpress" — all three fail
    on the type alone but succeed on the account's `readable` label against the
    carrier's `human_readable`. Matching on both resolved 8 of 9 real accounts;
    the one holdout ("FedEx Default") was covered by the same account's plain
    FedEx entry.

    Because that mapping is demonstrably lossy, the result is meant for ordering
    and flagging, not for hiding carriers outright — a carrier wrongly dropped
    here is a service the user cannot buy through the app at all.
    """
    # Snapshot the label -> code map BEFORE fetching accounts. list_carrier_accounts
    # records the account-derived codes and their labels, which would otherwise
    # land in this reverse map and shadow the real carrier: "Evri" would resolve
    # to "hermes" (an account type) instead of "evri" (the carrier the service
    # catalogue is actually keyed by), and the carrier would drop out of the
    # picker entirely.
    by_label = {
        (v or "").casefold(): k for k, v in _carrier_name_map().items() if v
    }

    accounts = list_carrier_accounts()
    if accounts is None:
        return None
    codes: set[str] = set()
    for account in accounts:
        codes.add(account.carrier_code)
        matched = by_label.get((account.readable or "").casefold())
        if matched:
            codes.add(matched)
    return codes or None


def service_levels_for_carrier(carrier: str) -> list[ServiceLevel]:
    """Cached service levels for one carrier, matched case-insensitively so a
    CamelCase code from a rate resolves against the lowercase catalogue."""
    key = (carrier or "").casefold()
    return [s for s in _cached_service_levels() if s.carrier.casefold() == key]
