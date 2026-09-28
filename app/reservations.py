"""Reservation service: every public method is one serialized SQLite transaction.

The service is the single authority for availability and business rules. It
re-validates everything inside the write transaction, so a slot that looked free
during an earlier `check_availability` call is never trusted blindly.
"""

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from app import config, db
from app.rules import (
    ReservationError,
    allocate_tables,
    bookable_starts,
    describe_slot,
    format_time,
    generate_code,
    normalize_code,
    normalize_phone,
    parse_date,
    parse_time,
    slot_datetime,
    validate_name,
    validate_notes,
    validate_party_size,
    validate_slot,
    windows_overlap,
)

Clock = Callable[[], datetime]

# Rule failures where suggesting nearby valid times is still helpful (e.g. "7:15pm", "10:30pm").
_SUGGEST_ON = {"OUTSIDE_HOURS", "INVALID_INCREMENT", "PAST_TIME"}


def _utc_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_record(row: sqlite3.Row) -> dict:
    """The BRD §9 persisted-reservation shape."""
    return {
        "confirmation_code": row["confirmation_code"],
        "date": row["service_date"],
        "time": row["start_time"],
        "party_size": row["party_size"],
        "guest_name": row["guest_name"],
        "phone": row["phone"],
        "notes": row["notes"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


class ReservationService:
    def __init__(self, db_path: Path, clock: Clock, code_factory: Callable[[], str] = generate_code):
        self.db_path = db_path
        self.clock = clock
        self.code_factory = code_factory

    # --- queries -------------------------------------------------------------

    def check_availability(self, date_str: str, time_str: str, party_size: int) -> dict:
        now = self.clock()
        d, minutes = parse_date(date_str), parse_time(time_str)
        validate_party_size(party_size)
        conn = db.connect(self.db_path)
        try:
            try:
                validate_slot(d, minutes, now)
            except ReservationError as err:
                if err.code in _SUGGEST_ON:
                    err.details["alternatives"] = self._alternatives(conn, d, minutes, party_size, now)
                raise
            available = self._allocate(conn, d, minutes, party_size) is not None
            result = {
                "ok": True,
                "available": available,
                "date": d.isoformat(),
                "time": format_time(minutes),
                "party_size": party_size,
                "when": describe_slot(d, minutes),
            }
            if not available:
                result["alternatives"] = self._alternatives(conn, d, minutes, party_size, now)
            return result
        finally:
            conn.close()

    def get(self, code: str) -> dict:
        conn = db.connect(self.db_path)
        try:
            return to_record(self._load(conn, normalize_code(code)))
        finally:
            conn.close()

    def list_for_date(self, date_str: str) -> list[dict]:
        d = parse_date(date_str)
        conn = db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM reservations WHERE service_date = ? ORDER BY start_time, id", (d.isoformat(),)
            ).fetchall()
            return [to_record(r) for r in rows]
        finally:
            conn.close()

    # --- commands ------------------------------------------------------------

    def create(
        self,
        date_str: str,
        time_str: str,
        party_size: int,
        guest_name: str,
        phone: str,
        notes: str | None = None,
        *,
        code: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Create a reservation. With dry_run=True, run every check but write nothing and
        return a preview (no confirmation code) -- used for the confirmation step."""
        now = self.clock()
        d, minutes = parse_date(date_str), parse_time(time_str)
        validate_party_size(party_size)
        name = validate_name(guest_name)
        phone_digits = normalize_phone(phone)
        clean_notes = validate_notes(notes)

        conn = db.connect(self.db_path)
        try:
            with db.transaction(conn):
                validate_slot(d, minutes, now)
                self._check_one_per_date(conn, phone_digits, d)
                tables = self._allocate(conn, d, minutes, party_size)
                if tables is None:
                    raise ReservationError(
                        "SLOT_UNAVAILABLE",
                        "That time is fully booked for this party size.",
                        alternatives=self._alternatives(conn, d, minutes, party_size, now),
                    )
                if dry_run:
                    return {
                        "date": d.isoformat(), "time": format_time(minutes), "party_size": party_size,
                        "guest_name": name, "phone": phone.strip(), "notes": clean_notes,
                    }
                stamp = _utc_iso(now)
                reservation_id, final_code = self._insert_with_unique_code(
                    conn,
                    code,
                    (d.isoformat(), format_time(minutes), party_size, name, phone.strip(), phone_digits,
                     clean_notes, stamp, stamp),
                )
                self._assign_tables(conn, reservation_id, tables)
            return to_record(self._load(conn, final_code))
        finally:
            conn.close()

    def modify(
        self,
        code: str,
        *,
        date_str: str | None = None,
        time_str: str | None = None,
        party_size: int | None = None,
        notes: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Change date/time/party size/notes (assumption A-6). All-or-nothing.
        With dry_run=True, run every check but write nothing and return the would-be record."""
        now = self.clock()
        conn = db.connect(self.db_path)
        try:
            with db.transaction(conn):
                row = self._load_active(conn, normalize_code(code), now)
                new_date = parse_date(date_str) if date_str is not None else date.fromisoformat(row["service_date"])
                new_minutes = parse_time(time_str) if time_str is not None else parse_time(row["start_time"])
                new_party = party_size if party_size is not None else row["party_size"]
                new_notes = validate_notes(notes) if notes is not None else row["notes"]
                validate_party_size(new_party)

                slot_changed = (
                    new_date.isoformat() != row["service_date"]
                    or format_time(new_minutes) != row["start_time"]
                    or new_party != row["party_size"]
                )
                if not slot_changed and new_notes == row["notes"]:
                    raise ReservationError("NO_CHANGES", "That reservation already has those details.")

                if slot_changed:
                    validate_slot(new_date, new_minutes, now)
                    if new_date.isoformat() != row["service_date"]:
                        self._check_one_per_date(conn, row["phone_normalized"], new_date)
                    tables = self._allocate(conn, new_date, new_minutes, new_party, exclude_id=row["id"])
                    if tables is None:
                        raise ReservationError(
                            "SLOT_UNAVAILABLE",
                            "The requested change is not available; the original reservation is unchanged.",
                            alternatives=self._alternatives(
                                conn, new_date, new_minutes, new_party, now, exclude_id=row["id"]
                            ),
                        )
                if dry_run:
                    return {
                        **to_record(row), "date": new_date.isoformat(), "time": format_time(new_minutes),
                        "party_size": new_party, "notes": new_notes,
                    }
                if slot_changed:
                    conn.execute("DELETE FROM reservation_tables WHERE reservation_id = ?", (row["id"],))
                    self._assign_tables(conn, row["id"], tables)

                conn.execute(
                    "UPDATE reservations SET service_date = ?, start_time = ?, party_size = ?, notes = ?, "
                    "updated_at = ? WHERE id = ?",
                    (new_date.isoformat(), format_time(new_minutes), new_party, new_notes, _utc_iso(now), row["id"]),
                )
            return to_record(self._load(conn, row["confirmation_code"]))
        except sqlite3.IntegrityError as exc:
            raise self._translate_integrity_error(exc)
        finally:
            conn.close()

    def cancel(self, code: str) -> dict:
        now = self.clock()
        conn = db.connect(self.db_path)
        try:
            with db.transaction(conn):
                row = self._load_active(conn, normalize_code(code), now)
                conn.execute(
                    "UPDATE reservations SET status = 'cancelled', updated_at = ? WHERE id = ?",
                    (_utc_iso(now), row["id"]),
                )
            return to_record(self._load(conn, row["confirmation_code"]))
        finally:
            conn.close()

    # --- internals -------------------------------------------------------------

    def _load(self, conn: sqlite3.Connection, code: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM reservations WHERE confirmation_code = ?", (code,)).fetchone()
        if row is None:
            raise ReservationError("NOT_FOUND", f"No reservation found with code {code}.")
        return row

    def _load_active(self, conn: sqlite3.Connection, code: str, now: datetime) -> sqlite3.Row:
        row = self._load(conn, code)
        if row["status"] == "cancelled":
            raise ReservationError("ALREADY_CANCELLED", f"Reservation {code} is already cancelled.")
        start = slot_datetime(date.fromisoformat(row["service_date"]), parse_time(row["start_time"]), now)
        if start <= now:
            raise ReservationError(
                "RESERVATION_IN_PAST", f"Reservation {code} has already started or passed and can't be changed."
            )
        return row

    def _check_one_per_date(self, conn: sqlite3.Connection, phone_digits: str, d: date) -> None:
        existing = conn.execute(
            "SELECT confirmation_code FROM reservations "
            "WHERE phone_normalized = ? AND service_date = ? AND status = 'confirmed'",
            (phone_digits, d.isoformat()),
        ).fetchone()
        if existing:
            raise ReservationError(
                "DUPLICATE_GUEST_DATE",
                "This phone number already has a reservation on that date (limit one per guest per date).",
            )

    def _free_tables(
        self, conn: sqlite3.Connection, d: date, minutes: int, exclude_id: int | None = None
    ) -> list[tuple[str, int]]:
        busy_rows = conn.execute(
            "SELECT r.id, r.start_time, rt.table_id FROM reservations r "
            "JOIN reservation_tables rt ON rt.reservation_id = r.id "
            "WHERE r.service_date = ? AND r.status = 'confirmed'",
            (d.isoformat(),),
        ).fetchall()
        busy = {
            row["table_id"]
            for row in busy_rows
            if row["id"] != exclude_id and windows_overlap(parse_time(row["start_time"]), minutes)
        }
        tables = conn.execute("SELECT id, capacity FROM restaurant_tables").fetchall()
        return [(t["id"], t["capacity"]) for t in tables if t["id"] not in busy]

    def _allocate(
        self, conn: sqlite3.Connection, d: date, minutes: int, party_size: int, exclude_id: int | None = None
    ) -> list[str] | None:
        return allocate_tables(party_size, self._free_tables(conn, d, minutes, exclude_id))

    def _alternatives(
        self,
        conn: sqlite3.Connection,
        d: date,
        minutes: int,
        party_size: int,
        now: datetime,
        exclude_id: int | None = None,
    ) -> list[str]:
        """Real open start times within ±90 minutes on the same day, closest first (D-7)."""
        candidates = []
        for start in bookable_starts():
            if start == minutes or abs(start - minutes) > config.ALTERNATIVE_WINDOW_MINUTES:
                continue
            try:
                validate_slot(d, start, now)
            except ReservationError:
                continue
            if self._allocate(conn, d, start, party_size, exclude_id) is not None:
                candidates.append(start)
        candidates.sort(key=lambda s: (abs(s - minutes), s))
        return [format_time(s) for s in candidates[: config.MAX_ALTERNATIVES]]

    def _insert_with_unique_code(self, conn: sqlite3.Connection, code: str | None, values: tuple) -> tuple[int, str]:
        attempts = 1 if code else 5
        for _ in range(attempts):
            candidate = code or self.code_factory()
            try:
                cursor = conn.execute(
                    "INSERT INTO reservations (confirmation_code, service_date, start_time, party_size, guest_name, "
                    "phone, phone_normalized, notes, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)",
                    (candidate, *values),
                )
                return cursor.lastrowid, candidate
            except sqlite3.IntegrityError as exc:
                if "confirmation_code" in str(exc):
                    continue
                raise self._translate_integrity_error(exc)
        raise ReservationError("CODE_GENERATION_FAILED", "Could not generate a unique confirmation code.")

    @staticmethod
    def _assign_tables(conn: sqlite3.Connection, reservation_id: int, tables: list[str]) -> None:
        conn.executemany(
            "INSERT INTO reservation_tables (reservation_id, table_id) VALUES (?, ?)",
            [(reservation_id, t) for t in tables],
        )

    @staticmethod
    def _translate_integrity_error(exc: sqlite3.IntegrityError) -> ReservationError:
        if "phone_normalized" in str(exc):
            return ReservationError(
                "DUPLICATE_GUEST_DATE",
                "This phone number already has a reservation on that date (limit one per guest per date).",
            )
        return ReservationError("CONFLICT", "The reservation could not be saved; please try again.")


def seed_demo_data(service: ReservationService, include_scenarios: bool) -> None:
    """Seed BV-DEMO (BRD §8) and, optionally, bookings that make BRD §10.2 reproducible.

    With T6-1 held 19:00-21:00 and T6-2 held 20:30-22:30 on the demo day, a party of 6
    at 20:00 is full and the real alternatives are 21:00, 18:30 and 21:30.
    """
    demo_day = db.next_open_day(service.clock().date()).isoformat()
    service.create(demo_day, "19:00", 4, "Alex Rivera", "555-0123", "", code="BV-DEMO")
    if include_scenarios:
        service.create(demo_day, "19:00", 6, "Demo Seed One", "555-0901", "demo seed data", code="BV-SEDA")
        service.create(demo_day, "20:30", 6, "Demo Seed Two", "555-0902", "demo seed data", code="BV-SEDB")
