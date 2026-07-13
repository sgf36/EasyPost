"""Tracker creation/lookup + local persistence. No public webhook receiver —
status is refreshed by polling EasyPost on a timer or manual refresh (see
architecture notes in the project plan)."""

from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor


def create_tracker(tracking_code: str, carrier: str = ""):
    client = client_manager.get_client()
    return client.tracker.create(tracking_code=tracking_code, carrier=carrier or None)


def retrieve_tracker(tracker_id: str):
    client = client_manager.get_client()
    return client.tracker.retrieve(tracker_id)


def save_tracker_locally(tracker) -> None:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO trackers (
                id, mode, tracking_code, carrier, status, est_delivery_date,
                shipment_id, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, est_delivery_date=excluded.est_delivery_date,
                last_checked_at=datetime('now')
            """,
            (
                tracker.id,
                mode,
                getattr(tracker, "tracking_code", None),
                getattr(tracker, "carrier", None),
                getattr(tracker, "status", None),
                getattr(tracker, "est_delivery_date", None),
                getattr(tracker, "shipment_id", None),
            ),
        )


@dataclass
class TrackerRecord:
    id: str
    mode: str
    tracking_code: Optional[str]
    carrier: Optional[str]
    status: Optional[str]
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


def refresh_all_trackers() -> list:
    """Re-fetches every locally-saved tracker for the active mode from
    EasyPost and updates local status. Returns the refreshed EasyPost
    Tracker objects.
    """
    refreshed = []
    for record in list_trackers():
        tracker = retrieve_tracker(record.id)
        save_tracker_locally(tracker)
        refreshed.append(tracker)
    return refreshed
