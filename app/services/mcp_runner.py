"""Carry out an approval a human has just accepted.

Deliberately re-checks everything rather than trusting the queued row. The row
was written by a process the agent can talk to; by the time a person clicks
Approve, the mode may have changed, the setting may have been switched off, or
the request may have aged past its rate. Re-validating here means the only
thing the queue contributes is the *intent*, not the authority.

Two rules about the record this leaves behind, because the daily ceiling is
only as honest as it is:

* Once EasyPost has taken the money, the request is ``done`` and counts as
  spend, whatever happens afterwards. Saving the label locally is bookkeeping;
  if that fails the purchase is still reported, with a warning. Recording a
  bought label as ``rejected`` hid it from the ceiling and invited the agent
  to buy it again.
* When the purchase call fails in a way that does not prove nothing was
  charged (a timeout, a dropped connection, a 5xx), the request stays
  ``approved``, which the ceiling counts, rather than being written off.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Optional

from app.core import mcp_approvals
from app.core.client import client_manager
from app.core.settings import load_settings
from app.services.pickups import buy_pickup
from app.services.shipments import buy_shipment, refund_shipment, save_shipment_locally


class _Handler(NamedTuple):
    # Spends the money. Anything it raises means the purchase may not exist.
    purchase: Callable[[dict], object]
    # Runs only after `purchase` returned. Its failures never undo the spend.
    bookkeeping: Optional[Callable[[object, dict], None]] = None


def execute_approved(request_id: str, *, acknowledge_unchecked: bool = False) -> dict:
    """Carry out a pending request.

    `acknowledge_unchecked` says the person approving has been shown why the
    spending limits could not measure this purchase and accepted it. Without
    it such a request is handed back to the queue untouched.

    Approving a request that is no longer pending (a second click, or a click
    racing another) does nothing and reports the state the request is in.
    """
    request = mcp_approvals.get_request(request_id)
    if request is None:
        raise ValueError("That request no longer exists.")

    # One conditional UPDATE decides who carries this out. Everything after
    # this line runs at most once per claim.
    if not mcp_approvals.claim_for_execution(request_id):
        current = mcp_approvals.get_request(request_id)
        return {
            "status": current.status if current else "unknown",
            "request_id": request_id,
            "already_decided": True,
        }

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

    handler = _HANDLERS.get(request.action)
    if handler is None:
        mcp_approvals.set_status(request_id, "rejected", error="unsupported action")
        raise ValueError(f"Unsupported action: {request.action}")

    if request.action not in mcp_approvals.NON_CHARGING_ACTIONS:
        # Ceilings are re-applied: earlier approvals today may have used up the
        # daily allowance since this one was queued. This request's own claim
        # is excluded; other approvals in flight are not.
        try:
            reason = mcp_approvals.check_ceilings(
                request.amount, settings, request.currency,
                account_billed=bool((request.summary or {}).get("account_billed")),
                acknowledged=acknowledge_unchecked,
                exclude_request_id=request_id,
            )
        except (mcp_approvals.SpendLimitExceeded, mcp_approvals.CeilingNotChecked) as exc:
            # Nothing was attempted, so it goes back to the queue: the person
            # may raise a limit, or confirm the unchecked reason, and retry.
            mcp_approvals.release_claim(request_id, error=str(exc))
            raise
        if reason:
            mcp_approvals.audit(request.action, request.args,
                                f"limits not applied ({reason}); approver confirmed")

    try:
        result = handler.purchase(request.args)
    except Exception as exc:  # noqa: BLE001
        if _nothing_was_charged(exc):
            mcp_approvals.set_status(request_id, "rejected", error=str(exc))
            mcp_approvals.audit(request.action, request.args, f"failed: {exc}")
        else:
            mcp_approvals.set_status(request_id, "approved",
                                     error=f"outcome unknown, check EasyPost: {exc}")
            mcp_approvals.audit(request.action, request.args, f"outcome unknown: {exc}")
        raise

    # EasyPost has accepted the purchase. From here nothing may record it as
    # anything other than done.
    warning = None
    if handler.bookkeeping is not None:
        try:
            handler.bookkeeping(result, request.args)
        except Exception as exc:  # noqa: BLE001
            warning = str(exc) or type(exc).__name__

    summary = _summarise(result)
    if warning:
        summary["local_save_error"] = warning
    try:
        mcp_approvals.set_status(request_id, "done", result=summary)
    except Exception as exc:  # noqa: BLE001
        # The row is still 'approved', which the ceiling counts, so the spend
        # is not lost; the person still needs to hear that the label exists.
        warning = warning or str(exc) or type(exc).__name__
    mcp_approvals.audit(
        request.action, request.args,
        f"executed; local save failed: {warning}" if warning else "executed",
    )
    return {"status": "done", "request_id": request_id, "warning": warning}


def _nothing_was_charged(exc: Exception) -> bool:
    """True only when the failure proves EasyPost refused the purchase.

    A 4xx is EasyPost saying no. A timeout, a dropped connection or a 5xx may
    have been processed on EasyPost's side before the reply was lost, so those
    are not proof. An exception that is not EasyPost's own and carries no HTTP
    status was raised here, before any request was sent.
    """
    status = getattr(exc, "http_status", None)
    if isinstance(status, int):
        return 400 <= status < 500 and status != 408
    from easypost.errors import EasyPostError

    return not isinstance(exc, EasyPostError)


def _buy_shipment(args: dict):
    return buy_shipment(args["shipment_id"], args["rate_id"])


def _save_shipment(shipment, _args: dict) -> None:
    save_shipment_locally(shipment)


def _refund_shipment(args: dict):
    return refund_shipment(args["shipment_id"])


def _buy_pickup(args: dict):
    # EasyPost buys a pickup by carrier and service, not by rate id. Both are
    # read back from EasyPost's own rate rather than from anything queued, for
    # the same reason the approval summary is.
    client = client_manager.get_client()
    pickup = client.pickup.retrieve(args["pickup_id"])
    rates = getattr(pickup, "pickup_rates", None) or []
    match = next((r for r in rates if getattr(r, "id", None) == args["rate_id"]), None)
    if match is None:
        raise ValueError(
            f"Rate {args['rate_id']} is no longer one of the pickup rates on {args['pickup_id']}."
        )
    return buy_pickup(args["pickup_id"], match.carrier, match.service)


# Every action the approval queue can carry out. tests/test_mcp_runner.py runs
# each entry through to the EasyPost client, which is what would have caught
# buy_pickup being called with two arguments instead of three.
_HANDLERS: dict[str, _Handler] = {
    "buy_shipment": _Handler(_buy_shipment, _save_shipment),
    "refund_shipment": _Handler(_refund_shipment),
    "buy_pickup": _Handler(_buy_pickup),
}


def _summarise(result) -> dict:
    return {
        "id": getattr(result, "id", None),
        "tracking_code": getattr(result, "tracking_code", None),
        "status": getattr(result, "status", None),
    }
