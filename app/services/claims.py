"""File and track insurance claims for insured, lost/damaged/stolen shipments."""

from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor

CLAIM_TYPES = ["damage", "loss", "theft"]


def file_claim(
    *,
    tracking_code: str,
    claim_type: str,
    amount: str,
    description: str = "",
    contact_email: str = "",
    recipient_name: str = "",
):
    client = client_manager.get_client()
    return client.claim.create(
        tracking_code=tracking_code,
        type=claim_type,
        amount=amount,
        description=description or None,
        contact_email=contact_email or None,
        recipient_name=recipient_name or None,
    )


def retrieve_claim(claim_id: str):
    client = client_manager.get_client()
    return client.claim.retrieve(claim_id)


def cancel_claim(claim_id: str):
    client = client_manager.get_client()
    return client.claim.cancel(claim_id)


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
