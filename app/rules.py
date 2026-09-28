"""Pure business rules: validation, slot math, table allocation, normalization.

Nothing here touches the database, the clock, or the LLM, so every rule can be
unit-tested with plain values.
"""

import re
import secrets
from datetime import date, datetime, time, timedelta
from itertools import combinations

from app import config


class ReservationError(Exception):
    """A business-rule failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"ok": False, "error_code": self.code, "message": self.message, **self.details}


# --- Parsing & formatting -------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_date(value: str) -> date:
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        raise ReservationError("INVALID_DATE", f"'{value}' is not a date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ReservationError("INVALID_DATE", f"'{value}' is not a real calendar date.")


def parse_time(value: str) -> int:
    """Parse 'HH:MM' (24h) into minutes after midnight."""
    match = _TIME_RE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        raise ReservationError("INVALID_TIME", f"'{value}' is not a time in 24-hour HH:MM format.")
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        raise ReservationError("INVALID_TIME", f"'{value}' is not a valid time.")
    return hours * 60 + minutes


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def describe_slot(d: date, minutes: int) -> str:
    """Human-friendly 'Saturday, October 3 at 7:00 PM' so the agent never does date math."""
    t = time(minutes // 60, minutes % 60)
    return f"{d.strftime('%A, %B')} {d.day} at {t.strftime('%I:%M %p').lstrip('0')}"


# --- Guest details ----------------------------------------------------------

_NAME_ALLOWED = re.compile(r"^[^\W\d_]+(?:[ '\-.][^\W\d_]*)*$", re.UNICODE)
_PHONE_ALLOWED = re.compile(r"^[\d\s()+\-.]+$")


def validate_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    letters = sum(ch.isalpha() for ch in cleaned)
    if letters < 2 or len(cleaned) > 80 or not _NAME_ALLOWED.match(cleaned):
        raise ReservationError("INVALID_NAME", "Please provide the guest's name (letters only, at least 2).")
    return cleaned


def normalize_phone(phone: str) -> str:
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not _PHONE_ALLOWED.match(raw) or not 7 <= len(digits) <= 15:
        raise ReservationError("INVALID_PHONE", "Please provide a valid phone number (7-15 digits).")
    return digits


def validate_notes(notes: str | None) -> str:
    cleaned = (notes or "").strip()
    if len(cleaned) > config.MAX_NOTES_LENGTH:
        raise ReservationError(
            "NOTES_TOO_LONG", f"Notes must be {config.MAX_NOTES_LENGTH} characters or fewer."
        )
    return cleaned


# --- Confirmation codes -------------------------------------------------------

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L look-alikes


def generate_code() -> str:
    return "BV-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))


def normalize_code(code: str) -> str:
    """Accept 'bv-demo', 'BV DEMO', 'BVDEMO', ' BV-DEMO. ' -> 'BV-DEMO'."""
    compact = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    if compact.startswith("BV"):
        compact = compact[2:]
    return f"BV-{compact}"


# --- Slot rules -----------------------------------------------------------------


def bookable_starts() -> list[int]:
    return list(range(config.OPENING_MIN, config.LAST_SEATING_MIN + 1, config.SLOT_MINUTES))


def validate_party_size(party_size: int) -> None:
    if not isinstance(party_size, int) or isinstance(party_size, bool):
        raise ReservationError("INVALID_PARTY_SIZE", "Party size must be a whole number.")
    if party_size < config.MIN_PARTY:
        raise ReservationError("PARTY_TOO_SMALL", "Party size must be at least 1.")
    if party_size > config.MAX_PARTY:
        raise ReservationError(
            "PARTY_TOO_LARGE",
            f"Online reservations are limited to {config.MAX_PARTY} guests; larger parties must call the restaurant.",
        )


def validate_slot(d: date, minutes: int, now: datetime) -> None:
    """Enforce BR-1, BR-2, BR-5, BR-6 and the last-seating rule. `now` is restaurant-local."""
    today = now.date()
    if d < today:
        raise ReservationError("PAST_TIME", "That date is in the past.")
    if d > today + timedelta(days=config.MAX_DAYS_AHEAD):
        raise ReservationError(
            "TOO_FAR_AHEAD", f"Reservations can be made at most {config.MAX_DAYS_AHEAD} days in advance."
        )
    if d.weekday() not in config.OPEN_WEEKDAYS:
        raise ReservationError("CLOSED_DAY", "Bella Vista is closed on Mondays.")
    if minutes < config.OPENING_MIN or minutes > config.LAST_SEATING_MIN:
        raise ReservationError(
            "OUTSIDE_HOURS",
            f"Reservations start between {format_time(config.OPENING_MIN)} and "
            f"{format_time(config.LAST_SEATING_MIN)} (we close at {format_time(config.CLOSING_MIN)}).",
        )
    if minutes % config.SLOT_MINUTES:
        raise ReservationError("INVALID_INCREMENT", "Reservations are available on the hour and half hour.")
    if slot_datetime(d, minutes, now) <= now:
        raise ReservationError("PAST_TIME", "That time has already passed.")


def slot_datetime(d: date, minutes: int, now: datetime) -> datetime:
    return datetime.combine(d, time(minutes // 60, minutes % 60), tzinfo=now.tzinfo)


def windows_overlap(start_a: int, start_b: int) -> bool:
    """Two same-day reservations overlap if their 2-hour holds intersect."""
    return abs(start_a - start_b) < config.HOLD_MINUTES


# --- Table allocation -------------------------------------------------------------


def allocate_tables(party_size: int, free_tables: list[tuple[str, int]]) -> list[str] | None:
    """Pick tables for a party from the tables free for the whole 2-hour window.

    Parties of 1-6: smallest single table that fits (A-3), lowest id on ties.
    Parties of 7-8: a pair of tables (A-2) with the fewest total seats; among equally sized
    pairs, the one whose larger table is smallest (so 4+4 beats 2+6 and 6-tops stay free for
    parties of 5-6, which can't combine); then lowest ids.
    Returns None when nothing fits.
    """
    ordered = sorted(free_tables, key=lambda t: (t[1], t[0]))
    if party_size < config.MIN_COMBINED_PARTY:
        for table_id, capacity in ordered:
            if capacity >= party_size:
                return [table_id]
        return None
    pairs = [
        (a, b) for a, b in combinations(ordered, 2) if a[1] + b[1] >= party_size
    ]
    if not pairs:
        return None
    best = min(
        pairs,
        key=lambda p: (p[0][1] + p[1][1], max(p[0][1], p[1][1]), sorted([p[0][0], p[1][0]])),
    )
    return sorted([best[0][0], best[1][0]])
