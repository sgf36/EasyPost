"""File and track insurance claims for insured, lost/damaged/stolen shipments.

What the live API actually enforces, none of which the previous version did:

* ``contact_email`` and ``description`` are **required**, not optional as the
  documentation suggests. Sending ``None`` (which is what a blank field
  produced) fails with "contact_email: field required; description: field
  required".
* A **damage** or **theft** claim must carry at least one attachment:
  "At least one supporting documentation attachment is required for theft or
  damage claims." A loss claim does not.
* Attachments go in one of three separate fields —
  ``supporting_documentation_attachments``, ``invoice_attachments`` and
  ``email_evidence_attachments`` — each a list of **base64 strings** with no
  ``data:`` URI prefix. A single ``attachments`` field does not exist and is
  ignored silently, which reads exactly like the attachment requirement being
  unsatisfiable.
* EasyPost also enforces a filing window: "Claims for this insurance policy
  must be filed between 15-60 days relative to the date of insurance creation."
"""

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor

logger = logging.getLogger(__name__)

CLAIM_TYPES = ["damage", "loss", "theft"]

# Types EasyPost will not accept without supporting documentation.
TYPES_REQUIRING_ATTACHMENT = ("damage", "theft")

# How the payout is made. Documented values.
PAYMENT_METHODS = ("easypost_wallet", "mailed_check")

# Documented claim statuses, in roughly the order a claim moves through them.
CLAIM_STATUSES = (
    "submitted", "in_review", "needs_action",
    "approved", "approved_partial", "rejected", "cancelled",
)
# Statuses that will not change again on their own.
TERMINAL_CLAIM_STATUSES = ("approved", "approved_partial", "rejected", "cancelled")
# The claimant has to do something before the claim can progress.
ACTION_REQUIRED_STATUSES = ("needs_action",)

# EasyPost's filing window, counted from when the insurance was created.
CLAIM_WINDOW_DAYS = (15, 60)

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


class ClaimRequestError(ValueError):
    """Raised for a claim EasyPost would reject, before it is sent."""


def encode_attachment(path: str) -> str:
    """Read a file as the base64 string EasyPost expects.

    Plain base64, deliberately without a ``data:`` URI prefix — the endpoint
    wants the encoded bytes alone.
    """
    data = Path(path).read_bytes()
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ClaimRequestError(
            f"{Path(path).name} is larger than "
            f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
        )
    return base64.b64encode(data).decode("ascii")


def validate_claim(
    *,
    claim_type: str,
    contact_email: str = "",
    description: str = "",
    payment_method: Optional[str] = None,
    supporting_documentation_attachments: Optional[list[str]] = None,
    invoice_attachments: Optional[list[str]] = None,
    email_evidence_attachments: Optional[list[str]] = None,
    **_ignored,
) -> None:
    """Raise if EasyPost would reject this claim.

    Separate from :func:`file_claim` so the UI can check a form before asking
    the user to confirm filing, rather than surfacing a 400 afterwards.
    """
    if claim_type not in CLAIM_TYPES:
        raise ClaimRequestError(
            f"'{claim_type}' is not a claim type. Choose one of: "
            f"{', '.join(CLAIM_TYPES)}."
        )
    if not contact_email.strip():
        raise ClaimRequestError("A contact email address is required.")
    if not description.strip():
        raise ClaimRequestError("A description of what happened is required.")

    supporting = list(supporting_documentation_attachments or [])
    invoices = list(invoice_attachments or [])
    emails = list(email_evidence_attachments or [])
    if claim_type in TYPES_REQUIRING_ATTACHMENT and not (supporting or invoices or emails):
        raise ClaimRequestError(
            f"A {claim_type} claim needs at least one supporting document — "
            "a photo of the damage, an invoice, or emailed evidence."
        )
    if payment_method and payment_method not in PAYMENT_METHODS:
        raise ClaimRequestError(
            f"'{payment_method}' is not a payment method. Choose one of: "
            f"{', '.join(PAYMENT_METHODS)}."
        )


def file_claim(
    *,
    tracking_code: str,
    claim_type: str,
    amount: str,
    description: str = "",
    contact_email: str = "",
    recipient_name: str = "",
    payment_method: Optional[str] = None,
    supporting_documentation_attachments: Optional[list[str]] = None,
    invoice_attachments: Optional[list[str]] = None,
    email_evidence_attachments: Optional[list[str]] = None,
    reference: str = "",
):
    """File a claim. Attachments are base64 strings — see
    :func:`encode_attachment`."""
    supporting = list(supporting_documentation_attachments or [])
    invoices = list(invoice_attachments or [])
    emails = list(email_evidence_attachments or [])
    validate_claim(
        claim_type=claim_type,
        contact_email=contact_email,
        description=description,
        payment_method=payment_method,
        supporting_documentation_attachments=supporting,
        invoice_attachments=invoices,
        email_evidence_attachments=emails,
    )

    params = {
        "tracking_code": tracking_code,
        "type": claim_type,
        "amount": amount,
        "description": description.strip(),
        "contact_email": contact_email.strip(),
        "recipient_name": recipient_name or None,
        "reference": reference or None,
    }
    if payment_method:
        params["payment_method"] = payment_method
    if supporting:
        params["supporting_documentation_attachments"] = supporting
    if invoices:
        params["invoice_attachments"] = invoices
    if emails:
        params["email_evidence_attachments"] = emails

    client = client_manager.get_client()
    claim = client.claim.create(**params)
    save_claim_locally(claim)
    return claim


def retrieve_claim(claim_id: str):
    client = client_manager.get_client()
    return client.claim.retrieve(claim_id)


def cancel_claim(claim_id: str):
    """Withdraw a claim. Records the result locally — previously this was never
    called from anywhere, so a filed claim could not be withdrawn from the app
    at all."""
    client = client_manager.get_client()
    claim = client.claim.cancel(claim_id)
    save_claim_locally(claim)
    return claim


def claim_is_open(status: Optional[str]) -> bool:
    """Whether a claim can still change — and so is worth refreshing, and can
    still be cancelled."""
    return str(status or "").lower() not in TERMINAL_CLAIM_STATUSES


def claim_needs_action(status: Optional[str]) -> bool:
    """EasyPost is waiting on the claimant. Worth saying loudly: a claim parked
    in this state will simply never progress on its own."""
    return str(status or "").lower() in ACTION_REQUIRED_STATUSES


def save_claim_locally(claim) -> None:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO claims (id, mode, tracking_code, status, type, amount, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status
            """,
            (
                claim.id,
                mode,
                getattr(claim, "tracking_code", None),
                getattr(claim, "status", None),
                getattr(claim, "type", None),
                str(getattr(claim, "amount", "") or ""),
                getattr(claim, "description", None),
            ),
        )


@dataclass
class ClaimRecord:
    id: str
    mode: str
    tracking_code: Optional[str]
    status: Optional[str]
    type: Optional[str]
    amount: Optional[str]
    description: Optional[str]


_CLAIM_FIELDS = [f for f in ClaimRecord.__dataclass_fields__]


def list_claims() -> list[ClaimRecord]:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM claims WHERE mode = ? ORDER BY created_at DESC", (mode,)
        )
        rows = cur.fetchall()
    return [ClaimRecord(**{k: row[k] for k in _CLAIM_FIELDS}) for row in rows]


def refresh_claim_status(claim_id: str) -> Optional[str]:
    claim = retrieve_claim(claim_id)
    save_claim_locally(claim)
    return getattr(claim, "status", None)
