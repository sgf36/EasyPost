"""SQLite schema management and connection helper.

Local tables mirror EasyPost resources for fast search/reporting; the
EasyPost API remains the source of truth. Each row keeps the `mode`
(test/production) it was created under so test and live data never mix
in the same view.
"""

import sqlite3
from contextlib import contextmanager

from app.config import DATABASE_PATH, ensure_app_data_dir

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
"""


def get_connection() -> sqlite3.Connection:
    ensure_app_data_dir()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()
