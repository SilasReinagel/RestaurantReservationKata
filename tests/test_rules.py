from datetime import date

import pytest

from app.rules import (
    CODE_ALPHABET,
    ReservationError,
    allocate_tables,
    bookable_starts,
    describe_slot,
    generate_code,
    normalize_code,
    normalize_phone,
    parse_date,
    parse_time,
    validate_name,
    validate_party_size,
    validate_slot,
    windows_overlap,
)
from tests.conftest import NOW


def error_code(fn, *args):
    with pytest.raises(ReservationError) as exc:
        fn(*args)
    return exc.value.code


# --- slot validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "day, time_str, expected",
    [
        ("2026-10-05", "19:00", "CLOSED_DAY"),        # Monday
        ("2026-10-03", "16:30", "OUTSIDE_HOURS"),
        ("2026-10-03", "22:00", "OUTSIDE_HOURS"),      # closing time is not a start time
        ("2026-10-03", "19:15", "INVALID_INCREMENT"),
        ("2026-09-28", "19:00", "PAST_TIME"),          # yesterday
        ("2026-09-29", "11:30", "OUTSIDE_HOURS"),      # today, before opening
        ("2026-11-29", "19:00", "TOO_FAR_AHEAD"),      # day 61
    ],
)
def test_validate_slot_rejects(day, time_str, expected):
    assert error_code(validate_slot, date.fromisoformat(day), parse_time(time_str), NOW) == expected


@pytest.mark.parametrize(
    "day, time_str",
    [
        ("2026-09-29", "17:00"),  # later today
        ("2026-10-04", "21:30"),  # Sunday, last seating
        ("2026-11-28", "19:00"),  # day 60 is inclusive
    ],
)
def test_validate_slot_accepts(day, time_str):
    validate_slot(date.fromisoformat(day), parse_time(time_str), NOW)


def test_same_day_slot_that_already_started_is_past():
    evening = NOW.replace(hour=19, minute=5)
    assert error_code(validate_slot, NOW.date(), parse_time("19:00"), evening) == "PAST_TIME"


def test_bookable_starts_run_17_to_2130():
    starts = bookable_starts()
    assert starts[0] == 17 * 60 and starts[-1] == 21 * 60 + 30 and len(starts) == 10


@pytest.mark.parametrize("size, expected", [(0, "PARTY_TOO_SMALL"), (9, "PARTY_TOO_LARGE"), (True, "INVALID_PARTY_SIZE")])
def test_party_size_limits(size, expected):
    assert error_code(validate_party_size, size) == expected


def test_parse_errors():
    assert error_code(parse_date, "Friday") == "INVALID_DATE"
    assert error_code(parse_date, "2026-02-30") == "INVALID_DATE"
    assert error_code(parse_time, "7pm") == "INVALID_TIME"
    assert error_code(parse_time, "25:00") == "INVALID_TIME"


def test_windows_overlap_is_two_hours():
    assert windows_overlap(19 * 60, 20 * 60 + 30)
    assert not windows_overlap(19 * 60, 21 * 60)  # back-to-back is fine


def test_describe_slot():
    assert describe_slot(date(2026, 10, 3), 19 * 60) == "Saturday, October 3 at 7:00 PM"


# --- allocation -----------------------------------------------------------------------

ALL = [("T2-1", 2), ("T2-2", 2), ("T4-1", 4), ("T4-2", 4), ("T6-1", 6), ("T6-2", 6)]


@pytest.mark.parametrize(
    "party, free, expected",
    [
        (1, ALL, ["T2-1"]),
        (3, ALL, ["T4-1"]),
        (5, ALL, ["T6-1"]),
        (2, [("T4-2", 4), ("T6-1", 6)], ["T4-2"]),       # falls back to a larger table
        (6, [("T4-1", 4), ("T2-1", 2)], None),           # 1-6 never combine
        (7, ALL, ["T4-1", "T4-2"]),                      # 4+4 beats 2+6: same seats, keeps the 6-top free
        (8, ALL, ["T4-1", "T4-2"]),
        (8, [("T2-1", 2), ("T4-1", 4), ("T6-1", 6)], ["T2-1", "T6-1"]),  # only one 4-top free -> 2+6
        (8, [("T4-1", 4), ("T6-1", 6), ("T6-2", 6)], ["T4-1", "T6-1"]),  # fewest seats still wins first (10 < 12)
        (8, [("T2-1", 2), ("T4-1", 4)], None),
    ],
)
def test_allocate_tables(party, free, expected):
    assert allocate_tables(party, free) == expected


# --- guest details & codes ------------------------------------------------------------


@pytest.mark.parametrize("name", ["José", "O'Brien", "Mary-Kate Smith", "J. R. Tolkien", "Cher"])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "A", "1234", "<script>", "x" * 81])
def test_invalid_names(name):
    assert error_code(validate_name, name) == "INVALID_NAME"


@pytest.mark.parametrize(
    "phone, digits", [("555-0123", "5550123"), ("(555) 012-3456", "5550123456"), ("+44 20 7946 0958", "442079460958")]
)
def test_phone_normalization(phone, digits):
    assert normalize_phone(phone) == digits


@pytest.mark.parametrize("phone", ["12345", "call me", "555-01x3", "1" * 16])
def test_invalid_phone(phone):
    assert error_code(normalize_phone, phone) == "INVALID_PHONE"


@pytest.mark.parametrize("raw", ["BV-DEMO", "bv-demo", "BV DEMO", "bvdemo", " BV-DEMO. ", "DEMO"])
def test_normalize_code(raw):
    assert normalize_code(raw) == "BV-DEMO"


def test_generated_codes_are_readable():
    code = generate_code()
    assert code.startswith("BV-") and len(code) == 7 and all(ch in CODE_ALPHABET for ch in code[3:])
