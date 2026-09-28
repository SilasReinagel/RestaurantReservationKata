import sqlite3
import threading

import pytest

from app import db
from app.reservations import ReservationService, seed_demo_data
from app.rules import ReservationError
from tests.conftest import NOW, book


def error_code(fn, *args, **kwargs):
    with pytest.raises(ReservationError) as exc:
        fn(*args, **kwargs)
    return exc.value


# --- create / get -------------------------------------------------------------------


def test_create_persists_brd_schema(service):
    record = book(service, notes="  anniversary  ")
    assert set(record) == {
        "confirmation_code", "date", "time", "party_size", "guest_name", "phone", "notes", "status", "created_at"
    }
    assert record["status"] == "confirmed" and record["notes"] == "anniversary"
    assert record["created_at"] == "2026-09-29T16:00:00Z"  # noon New York in UTC
    assert service.get(record["confirmation_code"].lower()) == record


def test_create_survives_restart(db_path, clock):
    record = book(ReservationService(db_path, clock))
    assert ReservationService(db_path, clock).get(record["confirmation_code"]) == record


def test_get_unknown_code(service):
    assert error_code(service.get, "BV-ZZZZ").code == "NOT_FOUND"


def test_create_enforces_rules_in_code(service):
    assert error_code(book, service, date="2026-10-05").code == "CLOSED_DAY"
    assert error_code(book, service, party=9).code == "PARTY_TOO_LARGE"
    assert error_code(book, service, phone="n/a").code == "INVALID_PHONE"


def test_one_reservation_per_phone_per_date(service):
    book(service, phone="555-0100")
    err = error_code(book, service, time="21:00", phone="(555) 0100")  # same digits, different format
    assert err.code == "DUPLICATE_GUEST_DATE"
    book(service, date="2026-10-04", phone="555-0100")  # another date is fine


def test_rebooking_after_cancel_is_allowed(service):
    first = book(service)
    service.cancel(first["confirmation_code"])
    assert book(service)["status"] == "confirmed"


def test_code_collision_retries(db_path, clock):
    codes = iter(["BV-AAAA", "BV-AAAA", "BV-BBBB"])
    service = ReservationService(db_path, clock, code_factory=lambda: next(codes))
    assert book(service, phone="555-1111")["confirmation_code"] == "BV-AAAA"
    assert book(service, phone="555-2222")["confirmation_code"] == "BV-BBBB"


# --- availability, 2-hour holds, tables ------------------------------------------------


def fill_six_tops(service, date="2026-10-03", time="20:00"):
    book(service, date=date, time=time, party=6, phone="555-0001")
    book(service, date=date, time=time, party=6, phone="555-0002")


def test_two_hour_hold_blocks_overlapping_starts(service):
    fill_six_tops(service, time="19:00")
    assert not service.check_availability("2026-10-03", "20:30", 6)["available"]
    assert service.check_availability("2026-10-03", "21:00", 6)["available"]  # back-to-back
    assert service.check_availability("2026-10-03", "20:30", 4)["available"]  # other tables unaffected


def test_unavailable_with_no_real_alternatives_returns_empty_list(service):
    fill_six_tops(service, time="20:00")
    result = service.check_availability("2026-10-03", "20:00", 6)
    # Every start in 18:30..21:30 overlaps a 20:00-22:00 hold, so there is nothing real to offer.
    assert result["available"] is False and result["alternatives"] == []


def test_alternatives_must_be_free_for_the_full_two_hours(service):
    fill_six_tops(service, time="19:00")
    # Window is 18:00..21:00; every start before 21:00 collides with the 19:00-21:00 holds.
    assert service.check_availability("2026-10-03", "19:30", 6)["alternatives"] == ["21:00"]
    assert service.check_availability("2026-10-03", "20:00", 6)["alternatives"] == ["21:00", "21:30"]


def test_create_unavailable_error_carries_alternatives(service):
    fill_six_tops(service, time="19:00")
    err = error_code(book, service, time="19:30", party=6, phone="555-0003")
    assert err.code == "SLOT_UNAVAILABLE" and err.details["alternatives"] == ["21:00"]


def test_off_grid_or_after_hours_requests_suggest_nearby_times(service):
    err = error_code(service.check_availability, "2026-10-03", "19:15", 2)
    assert err.code == "INVALID_INCREMENT" and err.details["alternatives"][:2] == ["19:00", "19:30"]
    err = error_code(service.check_availability, "2026-10-03", "22:30", 2)
    assert err.code == "OUTSIDE_HOURS" and err.details["alternatives"] == ["21:30", "21:00"]


def test_large_party_combines_two_tables(service, db_path):
    record = book(service, party=8)
    conn = db.connect(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT table_id FROM reservation_tables rt JOIN reservations r ON r.id = rt.reservation_id "
        "WHERE r.confirmation_code = ?", (record["confirmation_code"],))]
    conn.close()
    assert sorted(tables) == ["T4-1", "T4-2"]  # 4+4 preferred over 2+6, keeping 6-tops for parties of 5-6


def test_large_parties_leave_six_tops_for_parties_of_six(service):
    book(service, party=8, phone="555-0001")
    book(service, party=7, phone="555-0002")
    # Both big parties used 4-tops, so both 6-tops remain for parties that can't combine.
    assert service.check_availability("2026-10-03", "19:00", 6)["available"]
    book(service, party=6, phone="555-0003")
    book(service, party=6, phone="555-0004")


def test_capacity_is_exhausted_by_real_tables(service):
    # 12 tables; after 12 parties of 2 the room is full at 19:00 even though seats remain unused.
    for i in range(12):
        book(service, party=2, phone=f"555-10{i:02d}")
    assert service.check_availability("2026-10-03", "19:00", 2)["available"] is False
    assert service.check_availability("2026-10-03", "21:00", 2)["available"] is True


# --- dry run (used by the confirmation step) ---------------------------------------------


def test_create_dry_run_validates_but_writes_nothing(service):
    preview = service.create("2026-10-03", "19:00", 4, "  Alex   Rivera ", "555-0100", " anniversary ", dry_run=True)
    assert preview == {"date": "2026-10-03", "time": "19:00", "party_size": 4, "guest_name": "Alex Rivera",
                       "phone": "555-0100", "notes": "anniversary"}
    assert service.list_for_date("2026-10-03") == []
    fill_six_tops(service, time="19:00")
    err = error_code(service.create, "2026-10-03", "19:00", 6, "Sam", "555-0003", dry_run=True)
    assert err.code == "SLOT_UNAVAILABLE"


def test_modify_dry_run_validates_but_writes_nothing(service):
    record = book(service)
    preview = service.modify(record["confirmation_code"], time_str="20:00", party_size=8, dry_run=True)
    assert (preview["time"], preview["party_size"]) == ("20:00", 8)
    assert service.get(record["confirmation_code"]) == record
    assert error_code(service.modify, record["confirmation_code"], date_str="2026-10-05", dry_run=True).code == "CLOSED_DAY"


# --- modify ---------------------------------------------------------------------------


def test_modify_time_and_party(service):
    record = book(service)
    updated = service.modify(record["confirmation_code"], time_str="20:00", party_size=6)
    assert (updated["time"], updated["party_size"]) == ("20:00", 6)


def test_modify_does_not_conflict_with_itself(service):
    # Fill every 6-top except the one this reservation holds; a 30-minute shift must still work.
    mine = book(service, time="19:00", party=6, phone="555-0001")
    book(service, time="19:00", party=6, phone="555-0002")
    assert service.modify(mine["confirmation_code"], time_str="19:30")["time"] == "19:30"


def test_failed_modify_leaves_original_untouched(service):
    record = book(service, time="17:00", party=4)
    fill_six_tops(service, time="20:00")
    err = error_code(service.modify, record["confirmation_code"], time_str="20:00", party_size=6)
    assert err.code == "SLOT_UNAVAILABLE"
    assert service.get(record["confirmation_code"]) == record


def test_modify_growing_to_combined_tables_and_back(service):
    record = book(service, party=4)
    assert service.modify(record["confirmation_code"], party_size=8)["party_size"] == 8
    assert service.modify(record["confirmation_code"], party_size=3)["party_size"] == 3


def test_modify_rules(service, clock):
    record = book(service, date="2026-09-29", time="19:00")
    code = record["confirmation_code"]
    assert error_code(service.modify, code, time_str="19:00").code == "NO_CHANGES"
    assert error_code(service.modify, code, date_str="2026-10-05").code == "CLOSED_DAY"
    book(service, date="2026-10-03", phone=record["phone"])
    assert error_code(service.modify, code, date_str="2026-10-03").code == "DUPLICATE_GUEST_DATE"
    assert service.modify(code, notes="window if possible")["notes"] == "window if possible"
    clock.now = NOW.replace(hour=19, minute=30)
    assert error_code(service.modify, code, time_str="21:00").code == "RESERVATION_IN_PAST"


# --- cancel ---------------------------------------------------------------------------


def test_cancelled_reservation_cannot_be_cancelled_or_modified_again(service):
    first = book(service, date="2026-10-04", party=6)
    assert service.cancel(first["confirmation_code"])["status"] == "cancelled"
    assert error_code(service.cancel, first["confirmation_code"]).code == "ALREADY_CANCELLED"
    assert error_code(service.modify, first["confirmation_code"], time_str="20:00").code == "ALREADY_CANCELLED"


def test_cancel_frees_slot_for_others(service):
    book(service, time="19:00", party=6, phone="555-0001")
    blocker = book(service, time="19:00", party=6, phone="555-0002")
    assert not service.check_availability("2026-10-03", "19:00", 6)["available"]
    service.cancel(blocker["confirmation_code"])
    assert service.check_availability("2026-10-03", "19:00", 6)["available"]


# --- concurrency ------------------------------------------------------------------------


def test_concurrent_bookings_never_double_book(db_path, clock):
    """20 threads race for the two 6-tops at the same time: exactly two may win."""
    barrier = threading.Barrier(20)
    results = []

    def attempt(i):
        svc = ReservationService(db_path, clock)
        barrier.wait()
        try:
            svc.create("2026-10-03", "19:00", 6, "Racer Guest", f"555-20{i:02d}", "")
            results.append("ok")
        except ReservationError as err:
            results.append(err.code)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 2
    assert results.count("SLOT_UNAVAILABLE") == 18


def test_database_enforces_one_per_date_even_without_service(service, db_path):
    book(service, phone="555-0100")
    conn = db.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reservations (confirmation_code, service_date, start_time, party_size, guest_name, phone, "
            "phone_normalized, notes, status, created_at, updated_at) VALUES "
            "('BV-XXXX', '2026-10-03', '21:00', 2, 'X', '5550100', '5550100', '', 'confirmed', '', '')"
        )
    conn.close()


# --- seed -----------------------------------------------------------------------------------


def test_seed_reproduces_brd_examples(service):
    seed_demo_data(service, include_scenarios=True)
    demo = service.get("BV-DEMO")
    assert (demo["date"], demo["time"], demo["party_size"], demo["guest_name"]) == ("2026-09-30", "19:00", 4, "Alex Rivera")
    # BRD §10.2: "Table for 6 tomorrow at 8pm?" -> full; real options include 6:30pm and 9:00pm.
    result = service.check_availability("2026-09-30", "20:00", 6)
    assert result["available"] is False and result["alternatives"] == ["21:00", "18:30", "21:30"]


def test_seed_phone_conflict_is_limited_to_the_demo_day(service):
    """Documented in the README: BV-DEMO keeps the BRD's phone 555-0123, which BRD §10.1 also uses."""
    seed_demo_data(service, include_scenarios=True)
    err = error_code(service.create, "2026-09-30", "21:00", 2, "Alex Rivera", "555-0123")
    assert err.code == "DUPLICATE_GUEST_DATE"
    assert service.create("2026-10-03", "19:00", 4, "Alex Rivera", "555-0123")["status"] == "confirmed"


def test_seed_skips_monday(tmp_path, clock):
    clock.now = NOW.replace(day=27)  # Sunday -> tomorrow is Monday
    path = tmp_path / "s.db"
    db.init_db(path)
    svc = ReservationService(path, clock)
    seed_demo_data(svc, include_scenarios=False)
    assert svc.get("BV-DEMO")["date"] == "2026-09-29"  # Tuesday


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "i.db"
    assert db.init_db(path) is True
    assert db.init_db(path) is False
