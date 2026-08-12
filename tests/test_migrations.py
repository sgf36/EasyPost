"""Schema migrations against a database that already exists.

`CREATE TABLE IF NOT EXISTS` does nothing to a table that is already there, so
a column added to SCHEMA reaches a fresh install and silently misses every
existing one — then fails at runtime on the first write. These tests build an
old-shaped database on purpose and check it is brought forward.
"""

import sqlite3

import pytest

from app.core import db as db_module

# The trackers table exactly as it shipped before status_detail was added.
OLD_TRACKERS = """
CREATE TABLE trackers (
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
CREATE TABLE addresses (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    label TEXT, name TEXT, company TEXT, street1 TEXT, street2 TEXT,
    city TEXT, state TEXT, zip TEXT, country TEXT, phone TEXT, email TEXT,
    verified INTEGER DEFAULT 0,
    is_favorite INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database in the shape that shipped, populated with real-looking rows."""
    path = tmp_path / "easypost_desktop.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_TRACKERS)
    conn.execute(
        "INSERT INTO trackers (id, mode, tracking_code, status) VALUES (?,?,?,?)",
        ("trk_1", "test", "EZ1000000001", "in_transit"),
    )
    # Two addresses stored as verified back when failures were never detected.
    conn.executemany(
        "INSERT INTO addresses (id, mode, street1, verified) VALUES (?,?,?,?)",
        [("adr_1", "test", "1a Wroughton Road", 1),
         ("adr_2", "test", "10 Downing St", 1),
         ("adr_3", "test", "Somewhere", 0)],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_module, "DATABASE_PATH", path)
    monkeypatch.setattr(db_module, "ensure_app_data_dir", lambda: None)
    return path


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _query(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_a_missing_column_is_added_to_an_existing_table(old_db):
    assert "status_detail" not in _columns(old_db, "trackers")
    db_module.init_db()
    assert "status_detail" in _columns(old_db, "trackers")


def test_existing_rows_survive_the_migration(old_db):
    db_module.init_db()
    rows = _query(old_db, "SELECT id, tracking_code, status FROM trackers")
    assert rows == [("trk_1", "EZ1000000001", "in_transit")]


def test_untrustworthy_verified_flags_are_cleared(old_db):
    """Those flags were written when a failed verification was never detected,
    so they assert something that was never checked — including for addresses
    EasyPost actively rejects."""
    db_module.init_db()
    assert _query(old_db, "SELECT count(*) FROM addresses WHERE verified = 1") == [(0,)]
    # The addresses themselves are untouched; only the claim about them is.
    assert _query(old_db, "SELECT count(*) FROM addresses") == [(3,)]


def test_the_reset_runs_once_and_does_not_undo_a_later_reverification(old_db):
    db_module.init_db()
    conn = sqlite3.connect(old_db)
    conn.execute("UPDATE addresses SET verified = 1 WHERE id = 'adr_2'")
    conn.commit()
    conn.close()

    db_module.init_db()  # a later launch must not clear it again
    assert _query(old_db, "SELECT verified FROM addresses WHERE id='adr_2'") == [(1,)]


def test_the_database_is_backed_up_before_being_migrated(old_db):
    backup = old_db.with_suffix(old_db.suffix + ".pre-migration.bak")
    assert not backup.exists()
    db_module.init_db()
    assert backup.exists()
    # The backup holds the pre-migration state, not a copy of the new one.
    assert "status_detail" not in _columns(backup, "trackers")
    assert _query(backup, "SELECT count(*) FROM addresses WHERE verified = 1") == [(2,)]


def test_a_migrated_database_is_not_backed_up_again(old_db):
    db_module.init_db()
    backup = old_db.with_suffix(old_db.suffix + ".pre-migration.bak")
    before = backup.read_bytes()
    db_module.init_db()
    assert backup.read_bytes() == before


def test_init_is_idempotent(old_db):
    db_module.init_db()
    db_module.init_db()
    db_module.init_db()
    assert "status_detail" in _columns(old_db, "trackers")
    assert _query(old_db, "SELECT count(*) FROM trackers") == [(1,)]
