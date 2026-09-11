"""What the Dashboard shows, assembled from records the app already holds.

Nothing here calls EasyPost. The Dashboard is the page the app opens on, so it
has to appear at once and work with no connection, and everything it needs is
already in the local database: each purchase writes its shipment, and
Tracking's refresh keeps the trackers current.

The rules live in build_summary, a pure function over records, so which
parcels count as a problem, which refunds are pending and which shipments are
recent can be tested without a database or a window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.client import client_manager
from app.services.reports import total_spend_by_currency
from app.services.shipments import ShipmentRecord, is_refund_pending, list_shipments
from app.services.tracking import TrackerRecord, is_problem, is_terminal, list_trackers

# A failed or returned parcel is terminal, so the routine refresh never polls it
# again and nothing ever clears it. Without a window the landing page would keep
# a parcel that went wrong last year at the top for good, with no way to dismiss
# it, and a list that never empties soon stops being read.
PROBLEM_WINDOW_DAYS = 30

# Enough to recognise what went out recently. History holds the rest.
RECENT_SHIPMENTS = 5


@dataclass(frozen=True)
class DashboardSummary:
    mode: str
    shipment_count: int
    tracker_count: int
    open_trackers: int
    problems: tuple[TrackerRecord, ...]
    pending_refunds: tuple[ShipmentRecord, ...]
    spend_by_currency: dict[str, float]
    recent_shipments: tuple[ShipmentRecord, ...]

    @property
    def is_empty(self) -> bool:
        # Trackers count as well as shipments: someone who only follows parcels
        # posted elsewhere still has something for this page to show.
        return not self.shipment_count and not self.tracker_count


def build_summary(
    shipments: list[ShipmentRecord],
    trackers: list[TrackerRecord],
    *,
    mode: str,
    now: datetime,
) -> DashboardSummary:
    """Summarise one mode's records, given newest first as the list_* calls return them."""
    cutoff = now - timedelta(days=PROBLEM_WINDOW_DAYS)
    return DashboardSummary(
        mode=mode,
        shipment_count=len(shipments),
        tracker_count=len(trackers),
        open_trackers=sum(1 for t in trackers if not is_terminal(t.status)),
        problems=tuple(
            t for t in trackers if is_problem(t.status) and _seen_since(t, cutoff)
        ),
        pending_refunds=tuple(s for s in shipments if is_refund_pending(s.refund_status)),
        # Reports' own function, so the two pages can never disagree about spend.
        spend_by_currency=total_spend_by_currency(shipments),
        recent_shipments=tuple(shipments[:RECENT_SHIPMENTS]),
    )


def _seen_since(tracker: TrackerRecord, cutoff: datetime) -> bool:
    """Whether a problem tracker was last heard from inside the window.

    A terminal tracker is not polled again, so its last_checked_at is roughly
    when the app learnt of the problem. SQLite's datetime('now') writes it in
    UTC with no offset. A tracker with no readable time is kept: a problem that
    cannot be dated is still a problem, and hiding it is the worse mistake.
    """
    if not tracker.last_checked_at:
        return True
    try:
        seen = datetime.fromisoformat(tracker.last_checked_at)
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return seen >= cutoff


def load_summary(now: datetime | None = None) -> DashboardSummary:
    """The active mode's summary, read from the local database only."""
    return build_summary(
        list_shipments(),
        list_trackers(),
        mode=client_manager.active_mode,
        now=now or datetime.now(timezone.utc),
    )
