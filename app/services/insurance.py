"""Insurance: add coverage to an EasyPost-bought shipment, or insure a
shipment that was labelled outside EasyPost using its tracking code.

Two constraints are worth stating up front, because both are easy to get wrong
and neither fails gracefully:

* The amount is **always US dollars**, whatever currency the shipment itself is
  priced in, and EasyPost caps it at **5,000**. Verified live: 5000.00 is
  accepted and 5000.01 is rejected with "the amount provided is greater than the
  maximum allowed". The rejection arrives at purchase time, after the user has
  already confirmed spending money, so the limit is enforced in the UI too.
* Purchase is **not** instant. An insurance object starts `new` or `pending` and
  only later becomes `purchased` or `failed`, so treating the create call's
  return value as proof of cover is wrong.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor

logger = logging.getLogger(__name__)

# EasyPost's documented ceiling, confirmed against the live API.
INSURANCE_MAX_USD = 5000.0
INSURANCE_CURRENCY = "USD"

# The documented status values of an Insurance object.
INSURANCE_STATUSES = ("new", "pending", "purchased", "failed", "cancelled")
# Statuses that are still in flight — cover is not confirmed yet.
PENDING_STATUSES = ("new", "pending")
# Statuses that will not change again without another action.
TERMINAL_STATUSES = ("purchased", "failed", "cancelled")


class InsuranceAmountError(ValueError):
    """Raised for an amount EasyPost would reject, before any money moves."""


class StandaloneInsuranceUnavailable(RuntimeError):
    """Raised when the account cannot buy standalone insurance at all.

    Standalone insurance — covering a label bought outside EasyPost — is a
    per-account permission. Where it is switched off, EasyPost answers "Your
    account is not enabled for standalone insurance purchase" for every request,
    whatever the amount or address. That is a setting to take up with EasyPost
    rather than anything the user typed, so it is worth saying so plainly
    instead of showing the raw error and implying they got something wrong.
    """


def _is_not_enabled_error(exc: Exception) -> bool:
    from app.core.errors import format_api_error

    return "not enabled for standalone insurance" in format_api_error(exc).lower()


@dataclass
class InsuranceRecord:
    id: str
    mode: str
    shipment_id: Optional[str]
    tracking_code: Optional[str]
    carrier: Optional[str]
    amount: Optional[str]
    status: Optional[str]
    provider: Optional[str]
    reference: Optional[str]


def validate_amount(amount) -> str:
    """Normalise a declared value to the string EasyPost wants, or raise.

    Checked here rather than left to the API because the API's rejection lands
    *after* the user has confirmed a purchase — a poor moment to discover the
    figure was never allowed.
    """
    try:
        value = float(str(amount).replace(",", "").strip().lstrip("$"))
    except (TypeError, ValueError):
        raise InsuranceAmountError(f"'{amount}' is not a number.") from None
    if value <= 0:
        raise InsuranceAmountError("Insured value must be greater than zero.")
    if value > INSURANCE_MAX_USD:
        raise InsuranceAmountError(
            f"EasyPost insures up to ${INSURANCE_MAX_USD:,.0f} USD. "
            f"${value:,.2f} is above that limit."
        )
    return f"{value:.2f}"


def is_pending(insurance) -> bool:
    """Whether cover is still being arranged rather than confirmed."""
    return str(getattr(insurance, "status", "") or "").lower() in PENDING_STATUSES


def insure_existing_shipment(shipment_id: str, amount: str):
    """Add or increase insurance on a shipment already purchased through
    EasyPost."""
    amount = validate_amount(amount)
    client = client_manager.get_client()
    shipment = client.shipment.insure(shipment_id, amount=amount)
    save_insurance_locally(shipment, shipment_id=shipment_id, amount=amount)
    return shipment


def create_standalone_insurance(
    *, tracking_code: str, carrier: str, amount: str, reference: str = "", **extra
):
    """Insure a shipment/label that was NOT purchased through EasyPost,
    identified only by its tracking code and carrier.

    Note that this requires standalone insurance to be enabled on the EasyPost
    account. Where it is not, EasyPost answers "Your account is not enabled for
    standalone insurance purchase" — a permissions matter to take up with
    EasyPost, not something the app can work around.
    """
    amount = validate_amount(amount)
    client = client_manager.get_client()
    try:
        insurance = client.insurance.create(
            tracking_code=tracking_code,
            carrier=carrier,
            amount=amount,
            reference=reference or None,
            **extra,
        )
    except Exception as exc:
        if _is_not_enabled_error(exc):
            raise StandaloneInsuranceUnavailable(str(exc)) from exc
        raise
    save_insurance_locally(insurance)
    return insurance


def retrieve_insurance(insurance_id: str):
    """Re-read an insurance record. Needed because purchase is asynchronous:
    a `pending` record becomes `purchased` or `failed` later, and nothing tells
    the app when."""
    client = client_manager.get_client()
    insurance = client.insurance.retrieve(insurance_id)
    save_insurance_locally(insurance)
    return insurance


def refund_insurance(insurance_id: str):
    """Cancel an insurance policy and refund it."""
    client = client_manager.get_client()
    insurance = client.insurance.refund(insurance_id)
    save_insurance_locally(insurance)
    return insurance


def refresh_pending_insurances() -> int:
    """Re-read every locally known insurance that has not settled yet.

    Returns how many changed status. Best effort per record: one unreadable
    policy must not stop the rest from being refreshed.
    """
    changed = 0
    for record in list_insurances(pending_only=True):
        try:
            updated = retrieve_insurance(record.id)
        except Exception:
            logger.exception("Could not refresh insurance %s", record.id)
            continue
        if getattr(updated, "status", None) != record.status:
            changed += 1
    return changed


def save_insurance_locally(
    insurance, *, shipment_id: Optional[str] = None, amount: Optional[str] = None
) -> None:
    """Record an insurance policy locally.

    Accepts either an Insurance object or the Shipment returned by
    ``shipment.insure``; the latter carries the policy under `insurance` as a
    plain amount, with the shipment's own id as the handle.
    """
    identifier = getattr(insurance, "id", None)
    if not identifier:
        return
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO insurances (
                id, mode, shipment_id, tracking_code, carrier, amount,
                status, provider, reference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, amount=excluded.amount,
                provider=excluded.provider, updated_at=datetime('now')
            """,
            (
                identifier,
                client_manager.active_mode,
                shipment_id or getattr(insurance, "shipment_id", None),
                getattr(insurance, "tracking_code", None),
                getattr(insurance, "carrier", None),
                amount or _amount_of(insurance),
                getattr(insurance, "status", None),
                getattr(insurance, "provider", None),
                getattr(insurance, "reference", None),
            ),
        )


def _amount_of(insurance) -> Optional[str]:
    """`amount` on an Insurance, `insurance` on a Shipment — both are the same
    declared value under different names."""
    value = getattr(insurance, "amount", None)
    if value is None:
        value = getattr(insurance, "insurance", None)
    return str(value) if value is not None else None


def list_insurances(pending_only: bool = False) -> list[InsuranceRecord]:
    mode = client_manager.active_mode
    query = "SELECT * FROM insurances WHERE mode = ?"
    params: list = [mode]
    if pending_only:
        placeholders = ", ".join("?" for _ in PENDING_STATUSES)
        query += f" AND lower(coalesce(status, 'new')) IN ({placeholders})"
        params.extend(PENDING_STATUSES)
    query += " ORDER BY created_at DESC"

    with db_cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [
        InsuranceRecord(
            id=row["id"],
            mode=row["mode"],
            shipment_id=row["shipment_id"],
            tracking_code=row["tracking_code"],
            carrier=row["carrier"],
            amount=row["amount"],
            status=row["status"],
            provider=row["provider"],
            reference=row["reference"],
        )
        for row in rows
    ]


def update_shipment_insured_amount(shipment_id: str, amount: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE shipments SET insured_amount = ? WHERE id = ?",
            (amount, shipment_id),
        )
