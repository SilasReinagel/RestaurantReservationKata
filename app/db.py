"""SQLite connection handling, schema, and seed data."""

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurant_tables (
    id       TEXT PRIMARY KEY,
    capacity INTEGER NOT NULL CHECK (capacity IN (2, 4, 6))
);

CREATE TABLE IF NOT EXISTS reservations (
    id                INTEGER PRIMARY KEY,
    confirmation_code TEXT NOT NULL UNIQUE,
    service_date      TEXT NOT NULL,
    start_time        TEXT NOT NULL,
    party_size        INTEGER NOT NULL CHECK (party_size BETWEEN 1 AND 8),
    guest_name        TEXT NOT NULL,
    phone             TEXT NOT NULL,
    phone_normalized  TEXT NOT NULL,
    notes             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- BR-8: one active reservation per guest (phone) per date, enforced by the DB.
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_per_guest_per_date
    ON reservations (phone_normalized, service_date) WHERE status = 'confirmed';

CREATE INDEX IF NOT EXISTS ix_reservations_date ON reservations (service_date, status);

CREATE TABLE IF NOT EXISTS reservation_tables (
    reservation_id INTEGER NOT NULL REFERENCES reservations (id),
    table_id       TEXT NOT NULL REFERENCES restaurant_tables (id),
    PRIMARY KEY (reservation_id, table_id)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    # isolation_level=None disables sqlite3's implicit (DEFERRED) transactions so
    # that `transaction()` controls BEGIN IMMEDIATE / COMMIT explicitly.
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Serialize writers: BEGIN IMMEDIATE takes SQLite's write lock up front."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(db_path: Path) -> bool:
    """Create the schema and table inventory. Returns True if this is a fresh database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        with transaction(conn):
            fresh = conn.execute("SELECT COUNT(*) FROM restaurant_tables").fetchone()[0] == 0
            if fresh:
                conn.executemany(
                    "INSERT INTO restaurant_tables (id, capacity) VALUES (?, ?)", config.TABLE_INVENTORY
                )
        return fresh
    finally:
        conn.close()


def next_open_day(today: date) -> date:
    """'Tomorrow' for the demo seed, skipping Mondays (assumption A-11)."""
    day = today + timedelta(days=1)
    while day.weekday() not in config.OPEN_WEEKDAYS:
        day += timedelta(days=1)
    return day
