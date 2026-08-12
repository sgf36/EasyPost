"""Tracker creation/lookup + local persistence. No public webhook receiver —
status is refreshed by polling EasyPost on a timer or manual refresh (see
architecture notes in the project plan)."""

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor

logger = logging.getLogger(__name__)

# EasyPost's documented tracker statuses.
TRACKER_STATUSES = (
    "unknown", "pre_transit", "in_transit", "out_for_delivery",
    "available_for_pickup", "return_to_sender", "failure", "delivered",
    "cancelled", "error",
)

# Statuses a parcel does not move on from. Re-polling these spends requests to
# learn nothing, and on a long history that is the bulk of every refresh.
TERMINAL_STATUSES = ("delivered", "return_to_sender", "failure", "cancelled", "error")

# Statuses worth drawing attention to: the parcel is not going to arrive without
# somebody doing something.
PROBLEM_STATUSES = ("return_to_sender", "failure", "error")


def is_terminal(status: Optional[str]) -> bool:
    return str(status or "").lower() in TERMINAL_STATUSES


def is_problem(status: Optional[str]) -> bool:
    return str(status or "").lower() in PROBLEM_STATUSES


def create_tracker(tracking_code: str, carrier: str = ""):
    client = client_manager.get_client()
    return client.tracker.create(tracking_code=tracking_code, carrier=carrier or None)


def retrieve_tracker(tracker_id: str):
    client = client_manager.get_client()
    return client.tracker.retrieve(tracker_id)


def track_shipment(shipment) -> bool:
    """Record the tracker that came with a bought label.

    Buying a label always creates a tracker; this simply copies it into the
    app's own Tracking page so a shipment does not disappear the moment it is
    purchased. Best effort — the label is already paid for, so a bookkeeping
    failure here must never be reported as a failed purchase.
    """
    tracker = getattr(shipment, "tracker", None)
    if tracker is None:
        return False
    try:
        save_tracker_locally(tracker)
    except Exception:
        logger.exception("Could not record tracker for shipment %s",
                         getattr(shipment, "id", "?"))
        return False
    return True


def _get(obj, key: str, default=None):
    """Reads `key` from either an EasyPost SDK object (attribute access) or
    a plain dict (as delivered by a parsed webhook event payload)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def save_tracker_locally(tracker) -> None:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO trackers (
                id, mode, tracking_code, carrier, status, status_detail,
                est_delivery_date, shipment_id, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, status_detail=excluded.status_detail,
                est_delivery_date=excluded.est_delivery_date,
                last_checked_at=datetime('now')
            """,
            (
                _get(tracker, "id"),
                mode,
                _get(tracker, "tracking_code"),
                _get(tracker, "carrier"),
                _get(tracker, "status"),
                # The specific reason behind a status — "address_incorrect" under
                # a `failure`, say. Without it a stuck parcel says only that it
                # is stuck.
                _get(tracker, "status_detail"),
                _get(tracker, "est_delivery_date"),
                _get(tracker, "shipment_id"),
            ),
        )


@dataclass
class TrackerRecord:
    id: str
    mode: str
    tracking_code: Optional[str]
    carrier: Optional[str]
    status: Optional[str]
    status_detail: Optional[str]
    est_delivery_date: Optional[str]
    shipment_id: Optional[str]
    last_checked_at: Optional[str]


_TRACKER_FIELDS = [f for f in TrackerRecord.__dataclass_fields__]


def list_trackers() -> list[TrackerRecord]:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM trackers WHERE mode = ? ORDER BY created_at DESC", (mode,)
        )
        rows = cur.fetchall()
    return [TrackerRecord(**{k: row[k] for k in _TRACKER_FIELDS}) for row in rows]


def refresh_all_trackers(include_terminal: bool = False) -> list:
    """Re-read locally-saved trackers for the active mode and update status.

    Trackers in a terminal state are skipped by default: a delivered parcel is
    not going to change, and on a long history re-reading them is the bulk of
    every refresh for no new information. Pass ``include_terminal`` to force a
    full sweep.

    One tracker that cannot be read no longer aborts the whole refresh — that
    turned a single deleted or malformed tracker into a Tracking page that
    could never be updated at all.
    """
    refreshed = []
    for record in list_trackers():
        if not include_terminal and is_terminal(record.status):
            continue
        try:
            tracker = retrieve_tracker(record.id)
        except Exception:
            logger.exception("Could not refresh tracker %s", record.id)
            continue
        save_tracker_locally(tracker)
        refreshed.append(tracker)
    return refreshed
