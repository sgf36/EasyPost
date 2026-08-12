"""SQLite schema management and connection helper.

Local tables mirror EasyPost resources for fast search/reporting; the
EasyPost API remains the source of truth. Each row keeps the `mode`
(test/production) it was created under so test and live data never mix
in the same view.
"""

import logging
import shutil
import sqlite3
from contextlib import contextmanager

from app.config import DATABASE_PATH, ensure_app_data_dir

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS addresses (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    label TEXT,
    name TEXT,
    company TEXT,
    street1 TEXT,
    street2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    country TEXT,
    phone TEXT,
    email TEXT,
    verified INTEGER DEFAULT 0,
    is_favorite INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shipments (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT,
    to_address TEXT,
    from_address TEXT,
    carrier TEXT,
    service TEXT,
    rate_amount TEXT,
    rate_currency TEXT,
    tracking_code TEXT,
    label_url TEXT,
    insured_amount TEXT,
    refund_status TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trackers (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    tracking_code TEXT,
    carrier TEXT,
    status TEXT,
    -- The specific reason behind a status ("address_incorrect" under a
    -- `failure`). Without it a stuck parcel reports only that it is stuck.
    status_detail TEXT,
    est_delivery_date TEXT,
    shipment_id TEXT,
    last_checked_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pickups (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT,
    address TEXT,
    min_datetime TEXT,
    max_datetime TEXT,
    shipment_ids TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    tracking_code TEXT,
    status TEXT,
    type TEXT,
    amount TEXT,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT,
    num_shipments INTEGER,
    source_csv TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- HTS codes are global reference data (not test/production specific), so
-- unlike the tables above this has no `mode` column. Best-effort cache of
-- past live USITC lookups (app/services/hts_lookup.py) — a fallback for
-- offline/rate-limited searches, not a system of record, so no uniqueness
-- constraint on htsno (USITC's data legitimately repeats/nests htsno across
-- hierarchy levels).
CREATE TABLE IF NOT EXISTS hts_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    htsno TEXT,
    description TEXT,
    general_rate TEXT,
    special_rate TEXT,
    other_rate TEXT,
    units TEXT,
    indent INTEGER,
    cached_at TEXT DEFAULT (datetime('now'))
);

-- User-defined dimension/weight presets for quick reuse on Create Shipment.
-- Purely local convenience data, not an EasyPost resource, so no mode column.
CREATE TABLE IF NOT EXISTS saved_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    length REAL,
    width REAL,
    height REAL,
    weight REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Carrier predefined-package reference data (e.g. USPS flat rate boxes,
-- FedEx envelopes) from EasyPost's live Carrier Metadata endpoint
-- (app/services/packages.py). Global reference data like hts_cache — no
-- mode column. Unlike hts_cache this is a full replace-on-refresh cache
-- (not accumulated across searches), since each refresh fetches the
-- complete list per carrier rather than one keyword at a time.
CREATE TABLE IF NOT EXISTS predefined_packages_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    carrier TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    dimensions TEXT,
    max_weight REAL,
    cached_at TEXT DEFAULT (datetime('now'))
);

-- Insurance policies. Purchase is asynchronous — a policy starts `new` or
-- `pending` and only later settles to `purchased` or `failed` — so the create
-- call's return value is not proof of cover and the record has to be kept and
-- re-read. Amounts are always US dollars, whatever the shipment is priced in.
CREATE TABLE IF NOT EXISTS insurances (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    shipment_id TEXT,
    tracking_code TEXT,
    carrier TEXT,
    amount TEXT,
    status TEXT,
    provider TEXT,
    reference TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Carrier names from the same Carrier Metadata endpoint (app/services/
-- carriers.py). Keyed by the LOWERCASE carrier code the API returns
-- ("royalmailv3"), which is what makes carrier_display_name resolve at all.
-- Upserted rather than replaced, so names already learnt survive a refresh
-- that happens to be filtered to fewer carriers.
CREATE TABLE IF NOT EXISTS carriers_cache (
    name TEXT PRIMARY KEY,
    human_readable TEXT,
    cached_at TEXT DEFAULT (datetime('now'))
);

-- The services each carrier offers. A batch shipment is never rated by
-- EasyPost, so the service must be named at create time and a wrong name only
-- surfaces at purchase — this catalogue is what lets the user pick a real one
-- instead of typing it. Full replace-on-refresh, like
-- predefined_packages_cache.
CREATE TABLE IF NOT EXISTS service_levels_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    carrier TEXT NOT NULL,
    name TEXT NOT NULL,
    human_readable TEXT,
    dimensions TEXT,
    max_weight REAL,
    cached_at TEXT DEFAULT (datetime('now'))
);

-- Spend requests raised by an AI agent over MCP, awaiting human approval in
-- the desktop app. Nothing here is trusted: `summary_json` is re-fetched from
-- EasyPost at approval time rather than taken from whatever the agent said,
-- so an agent cannot misrepresent what it is asking to buy.
CREATE TABLE IF NOT EXISTS mcp_approvals (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    action TEXT NOT NULL,          -- buy_shipment | buy_pickup | refund | ...
    args_json TEXT NOT NULL,       -- exactly what the agent asked for
    summary_json TEXT,             -- independently verified detail, for display
    amount REAL,                   -- verified cost, for the spend ceiling
    currency TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending|approved|rejected|expired|done
    result_json TEXT,
    error TEXT,
    requested_at TEXT DEFAULT (datetime('now')),
    decided_at TEXT
);

-- Append-only record of every MCP tool invocation. Deliberately separate from
-- approvals: read-only calls never create an approval, but still need to be
-- auditable after the fact.
CREATE TABLE IF NOT EXISTS mcp_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT,
    tool TEXT NOT NULL,
    args_json TEXT,
    outcome TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def get_connection() -> sqlite3.Connection:
    ensure_app_data_dir()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added to tables that already existed in the field. `CREATE TABLE IF
# NOT EXISTS` does nothing to a table that is already there, so a new column in
# SCHEMA above reaches a fresh install and silently misses every existing one —
# which then fails at runtime on the first write. Each entry is (table, column,
# definition) and is applied only when genuinely absent.
_COLUMN_MIGRATIONS = [
    ("trackers", "status_detail", "TEXT"),
]


def _existing_columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _apply_column_migrations(conn) -> list[str]:
    """Add any missing columns. Returns what was added, for logging."""
    applied = []
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, column, definition in _COLUMN_MIGRATIONS:
        if table not in tables:
            continue  # created by SCHEMA with the column already present
        if column in _existing_columns(conn, table):
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        applied.append(f"{table}.{column}")
    return applied


def _reset_untrustworthy_verified_flags(conn) -> int:
    """Clear address `verified` flags written before verification worked.

    Until the address-verification fix, a failed verification was never
    detected, so every address in the book was stored as verified whether or not
    EasyPost had confirmed anything — including ones it actively rejects. Those
    stored flags are not merely stale, they are wrong, and leaving them in place
    would keep presenting an unverified address as deliverable.

    Clearing them costs the user nothing but a re-verify, and the alternative is
    continuing to assert something untrue. Runs once, guarded by a marker row.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
    )
    marker = "reset_verified_flags_v1"
    already = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (marker,)
    ).fetchone()
    if already:
        return 0
    cursor = conn.execute("UPDATE addresses SET verified = 0 WHERE verified = 1")
    conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (marker,))
    return cursor.rowcount or 0


def _backup_database() -> None:
    """Copy the database aside before the first migration touches it.

    Written once and never overwritten, so it preserves the state from before
    any migration in this version ran rather than being replaced on each
    launch. A backup that cannot be written is not worth failing start-up
    over — but it is worth recording that it did not happen."""
    backup = DATABASE_PATH.with_suffix(DATABASE_PATH.suffix + ".pre-migration.bak")
    if backup.exists() or not DATABASE_PATH.exists():
        return
    try:
        shutil.copy2(DATABASE_PATH, backup)
        logger.info("Database backed up to %s before migrating", backup)
    except OSError:
        logger.exception("Could not back up the database before migrating")


def _needs_migration(conn) -> bool:
    """Whether anything is actually pending, so an untouched database is not
    backed up and rewritten on every single launch."""
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, column, _definition in _COLUMN_MIGRATIONS:
        if table in tables and column not in _existing_columns(conn, table):
            return True
    if "schema_migrations" not in tables:
        return True
    return not conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", ("reset_verified_flags_v1",)
    ).fetchone()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        if _needs_migration(conn):
            _backup_database()
        added = _apply_column_migrations(conn)
        cleared = _reset_untrustworthy_verified_flags(conn)
    if added:
        logger.info("Added database columns: %s", ", ".join(added))
    if cleared:
        logger.info(
            "Cleared %d address verification flag(s) recorded before "
            "verification failures were detected; re-verify to confirm them.",
            cleared,
        )


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()
