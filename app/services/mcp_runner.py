"""Carry out an approval a human has just accepted.

Deliberately re-checks everything rather than trusting the queued row. The row
was written by a process the agent can talk to; by the time a person clicks
Approve, the mode may have changed, the setting may have been switched off, or
the request may have aged past its rate. Re-validating here means the only
thing the queue contributes is the *intent*, not the authority.
"""

from __future__ import annotations

from app.core import mcp_approvals
from app.core.client import client_manager
from app.core.settings import load_settings
from app.services.shipments import buy_shipment, refund_shipment, save_shipment_locally


def execute_approved(request_id: str) -> dict:
    request = mcp_approvals.get_request(request_id)
    if request is None:
        raise ValueError("That request no longer exists.")
    if request.status != "pending":
        # Guards against a double-click racing two purchases through.
        raise ValueError(f"That request is already {request.status}.")

    settings = load_settings()
    if not settings.mcp_allow_spending:
        mcp_approvals.set_status(request_id, "rejected", error="spending disabled")
        raise PermissionError("Agent spending is switched off, so this was not carried out.")

    if request.mode != client_manager.active_mode:
        # A request raised in test mode must never execute against production.
        mcp_approvals.set_status(request_id, "rejected", error="mode changed")
        raise PermissionError(
            f"This was requested in {request.mode} mode but {client_manager.active_mode} "
            "is now active. Rejected rather than carried out against the wrong account."
        )

    # Ceilings are re-applied: earlier approvals today may have used up the
    # daily allowance since this one was queued.
    mcp_approvals.check_ceilings(request.amount, settings)

    mcp_approvals.set_status(request_id, "approved")
    try:
        result = _dispatch(request)
    except Exception as exc:  # noqa: BLE001
        mcp_approvals.set_status(request_id, "rejected", error=str(exc))
        mcp_approvals.audit(request.action, request.args, f"failed: {exc}")
        raise

    mcp_approvals.set_status(request_id, "done", result=_summarise(result))
    mcp_approvals.audit(request.action, request.args, "executed")
    return {"status": "done", "request_id": request_id}


def _dispatch(request):
    action, args = request.action, request.args
    if action == "buy_shipment":
        shipment = buy_shipment(args["shipment_id"], args["rate_id"])
        save_shipment_locally(shipment)
        return shipment
    if action == "refund_shipment":
        return refund_shipment(args["shipment_id"])
    if action == "buy_pickup":
        from app.services.pickups import buy_pickup

        return buy_pickup(args["pickup_id"], args["rate_id"])
    raise ValueError(f"Unsupported action: {action}")


def _summarise(result) -> dict:
    return {
        "id": getattr(result, "id", None),
        "tracking_code": getattr(result, "tracking_code", None),
        "status": getattr(result, "status", None),
    }
