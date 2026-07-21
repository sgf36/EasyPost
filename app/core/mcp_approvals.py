"""Human-in-the-loop approval gate for money-spending MCP calls.

An AI agent connected over MCP can read this app's data and shop rates freely.
It cannot spend money. Anything that costs money is filed here as a *request*
and surfaced in the desktop app, where a person approves or rejects it.

Why not just prompt the user through MCP
----------------------------------------
MCP has an elicitation flow, but it is optional — clients may ignore it — and
more importantly the agent controls the text of what it asks. An agent that
has been prompt-injected by content it read (a recipient name, a CSV cell, a
tracking description) can word a confirmation to look innocuous, or route
around it entirely.

So the trust boundary sits outside anything the agent can influence:

1. The agent's arguments are recorded verbatim, and never displayed as fact.
2. What the *user* sees is re-fetched independently from EasyPost using only
   the ids supplied — carrier, service, amount, and both addresses as EasyPost
   reports them. An agent that lies about what it is buying is caught here,
   because the dialog does not repeat its claims.
3. Approval happens in this application's own window, rendered by this code.
4. Hard ceilings apply *before* a human ever sees the request. Something over
   the per-purchase or daily limit is refused outright rather than presented
   for confirmation, so no amount of persuasion in the agent's message can
   talk a tired human into it.

None of this makes an agent trustworthy. It makes an untrusted agent unable to
move money on its own.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from app.core.client import client_manager
from app.core.db import db_cursor

# Actions that cost money and therefore require approval. Anything not listed
# here is read-only or quote-only and runs without a gate.
SPENDING_ACTIONS = frozenset(
    {
        "buy_shipment",
        "buy_batch",
        "buy_pickup",
        "insure_shipment",
        "create_standalone_insurance",
        "refund_shipment",
    }
)

# Requests older than this are treated as abandoned. Prevents an approval
# queued days ago from being clicked long after its rate has expired.
APPROVAL_TTL_SECONDS = 3600


class SpendLimitExceeded(Exception):
    """Raised when a request breaches a ceiling. Never offered for approval."""


@dataclass
class ApprovalRequest:
    id: str
    mode: str
    action: str
    args: dict
    summary: dict
    amount: Optional[float]
    currency: Optional[str]
    status: str
    requested_at: str


def _row_to_request(row) -> ApprovalRequest:
    return ApprovalRequest(
        id=row["id"],
        mode=row["mode"],
        action=row["action"],
        args=json.loads(row["args_json"] or "{}"),
        summary=json.loads(row["summary_json"] or "{}"),
        amount=row["amount"],
        currency=row["currency"],
        status=row["status"],
        requested_at=row["requested_at"],
    )


def spent_today(mode: str) -> float:
    """Total approved-and-completed spend today, for the daily ceiling."""
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total FROM mcp_approvals
            WHERE mode = ? AND status = 'done' AND amount IS NOT NULL
              AND date(decided_at) = date('now')
            """,
            (mode,),
        )
        return float(cur.fetchone()["total"] or 0.0)


def check_ceilings(amount: Optional[float], settings) -> None:
    """Refuse outright, before a human is ever asked.

    A ceiling that can be argued past is not a ceiling. These raise rather
    than creating a pending request, so an over-limit purchase never appears
    as something a person can simply click through.
    """
    if amount is None:
        return
    mode = client_manager.active_mode
    per_purchase = float(getattr(settings, "mcp_max_purchase", 0) or 0)
    per_day = float(getattr(settings, "mcp_daily_limit", 0) or 0)

    if per_purchase and amount > per_purchase:
        raise SpendLimitExceeded(
            f"{amount:.2f} exceeds the per-purchase limit of {per_purchase:.2f}. "
            "Raise it in Settings if this is intended."
        )
    if per_day:
        already = spent_today(mode)
        if already + amount > per_day:
            raise SpendLimitExceeded(
                f"{amount:.2f} would take today's agent spend to "
                f"{already + amount:.2f}, over the daily limit of {per_day:.2f}."
            )


def create_request(action: str, args: dict, summary: dict, amount, currency) -> ApprovalRequest:
    """File a request for a human to decide on.

    `summary` must have been built from data fetched from EasyPost, not from
    the agent's arguments — see verify_* in app/services/mcp_verify.py.
    """
    request_id = f"apr_{uuid.uuid4().hex[:16]}"
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO mcp_approvals (id, mode, action, args_json, summary_json,
                                       amount, currency, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (request_id, mode, action, json.dumps(args), json.dumps(summary), amount, currency),
        )
    return ApprovalRequest(
        id=request_id, mode=mode, action=action, args=args, summary=summary,
        amount=amount, currency=currency, status="pending", requested_at="",
    )


def list_pending() -> list[ApprovalRequest]:
    expire_stale()
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM mcp_approvals WHERE status = 'pending' AND mode = ? "
            "ORDER BY requested_at ASC",
            (mode,),
        )
        return [_row_to_request(r) for r in cur.fetchall()]


def get_request(request_id: str) -> Optional[ApprovalRequest]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM mcp_approvals WHERE id = ?", (request_id,))
        row = cur.fetchone()
    return _row_to_request(row) if row else None


def expire_stale() -> int:
    """Time out requests nobody acted on, so a stale rate cannot be bought."""
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE mcp_approvals SET status = 'expired', decided_at = datetime('now')
            WHERE status = 'pending'
              AND (julianday('now') - julianday(requested_at)) * 86400 > ?
            """,
            (APPROVAL_TTL_SECONDS,),
        )
        return cur.rowcount


def set_status(request_id: str, status: str, result: Any = None, error: str = None) -> None:
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE mcp_approvals
            SET status = ?, decided_at = datetime('now'),
                result_json = COALESCE(?, result_json), error = COALESCE(?, error)
            WHERE id = ?
            """,
            (status, json.dumps(result) if result is not None else None, error, request_id),
        )


def audit(tool: str, args: dict, outcome: str) -> None:
    """Append-only log of every tool call, including read-only ones.

    Read-only calls never create an approval, so without this there would be
    no record that an agent had enumerated the address book.
    """
    try:
        mode = client_manager.active_mode
    except Exception:  # noqa: BLE001 - auditing must never break a call
        mode = None
    try:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO mcp_audit (mode, tool, args_json, outcome) VALUES (?, ?, ?, ?)",
                (mode, tool, json.dumps(args, default=str)[:4000], outcome),
            )
    except Exception:  # noqa: BLE001
        pass
