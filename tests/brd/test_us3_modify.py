"""US-3 - Modify an existing reservation.

AC-3.1 Guest provides confirmation code (or name + date; decision D-4: code only in this version)
AC-3.2 Agent validates new slot availability before committing the change
AC-3.3 Original slot is released; new slot is held
"""

import pytest

from app.agent import TOOLS
from app.reservations import seed_demo_data
from app.rules import ReservationError
from tests.brd.helpers import (
    DEMO_DAY, MONDAY, SATURDAY, SUNDAY, call, make_agent, modification, new_session, preview_then_confirm,
    result_of, say, tables_of,
)
from tests.conftest import NOW


def rejects(fn, *args, **kwargs) -> ReservationError:
    with pytest.raises(ReservationError) as exc:
        fn(*args, **kwargs)
    return exc.value


def mine(service, time="19:00", party=4, date=SATURDAY, phone="555-0199"):
    return service.create(date, time, party, "Alex Rivera", phone)


# --- AC-3.1 Guest provides the confirmation code -------------------------------------------------


@pytest.mark.parametrize("typed", ["BV-DEMO", "bv-demo", "BV DEMO", "demo"])
def test_tc_3_1_01_seeded_reservation_is_modified_by_code(service, typed):
    """US-3 / AC-3.1 / TC-3.1.01 [happy] BV-DEMO (BRD §8) found by code in any casing/format."""
    seed_demo_data(service, include_scenarios=False)
    _, confirmed, _ = preview_then_confirm(service, "modify_reservation", modification(typed, time="20:00"))
    assert confirmed["ok"] and service.get("BV-DEMO")["time"] == "20:00"


def test_tc_3_1_02_unknown_code_is_reported(service):
    """US-3 / AC-3.1 / TC-3.1.02 [failure]"""
    agent, llm = make_agent(service, call("modify_reservation", **modification("BV-ZZZZ", time="20:00")),
                            say("I can't find that code."))
    agent.handle(new_session(), "Move BV-ZZZZ to 8")
    assert result_of(llm, "call_1")["error_code"] == "NOT_FOUND"


def test_tc_3_1_03_lookup_by_name_and_date_is_not_offered():
    """US-3 / AC-3.1 / TC-3.1.03 [failure] D-4: the tools only accept a confirmation code."""
    for name in ("modify_reservation", "get_reservation", "cancel_reservation"):
        params = next(t for t in TOOLS if t["function"]["name"] == name)["function"]["parameters"]
        assert "confirmation_code" in params["required"]
        assert "guest_name" not in params["properties"] and "phone" not in params["properties"]


def test_tc_3_1_04_cancelled_reservation_cannot_be_modified(service):
    """US-3 / AC-3.1 / TC-3.1.04 [failure]"""
    code = mine(service)["confirmation_code"]
    service.cancel(code)
    assert rejects(service.modify, code, time_str="20:00").code == "ALREADY_CANCELLED"


def test_tc_3_1_05_started_reservation_cannot_be_modified(service, clock):
    """US-3 / AC-3.1 / TC-3.1.05 [failure]"""
    code = mine(service, date="2026-09-29")["confirmation_code"]
    clock.now = NOW.replace(hour=19, minute=0)
    assert rejects(service.modify, code, time_str="21:00").code == "RESERVATION_IN_PAST"


# --- AC-3.2 Validate new slot availability before committing --------------------------------------


def test_tc_3_2_01_change_is_previewed_then_committed(service):
    """US-3 / AC-3.2 / TC-3.2.01 [happy] The preview leaves the booking untouched; "yes" commits it."""
    record = mine(service)
    session = new_session()
    agent, llm = make_agent(
        service,
        call("modify_reservation", _id="preview", **modification(record["confirmation_code"], time="20:00")),
        say("Move to 8:00 PM?"),
        call("modify_reservation", _id="confirm", **modification(record["confirmation_code"], time="20:00")),
        say("Done."),
    )
    agent.handle(session, "Move it to 8")
    assert result_of(llm, "preview")["preview"]["time"] == "20:00"
    assert service.get(record["confirmation_code"]) == record
    agent.handle(session, "Yes")
    assert service.get(record["confirmation_code"])["time"] == "20:00"


@pytest.mark.parametrize("new_party, table_count", [(6, 1), (8, 2), (2, 1)])
def test_tc_3_2_02_party_size_change_is_reseated(service, db_path, new_party, table_count):
    """US-3 / AC-3.2 / TC-3.2.02 [happy] Growing to 8 combines tables; shrinking uses a smaller table."""
    code = mine(service)["confirmation_code"]
    assert service.modify(code, party_size=new_party)["party_size"] == new_party
    assert len(tables_of(db_path, code)) == table_count


def test_tc_3_2_03_unavailable_new_slot_leaves_original_unchanged(service, db_path):
    """US-3 / AC-3.2 / TC-3.2.03 [failure] Error carries real alternatives; nothing moves."""
    record = mine(service, time="17:00")
    before = tables_of(db_path, record["confirmation_code"])
    for phone in ("555-0001", "555-0002"):
        service.create(SATURDAY, "20:00", 6, "Other Guest", phone)
    err = rejects(service.modify, record["confirmation_code"], time_str="20:00", party_size=6)
    assert err.code == "SLOT_UNAVAILABLE" and "alternatives" in err.details
    assert service.get(record["confirmation_code"]) == record
    assert tables_of(db_path, record["confirmation_code"]) == before


@pytest.mark.parametrize("changes, code", [
    ({"date_str": MONDAY}, "CLOSED_DAY"),
    ({"time_str": "22:00"}, "OUTSIDE_HOURS"),
    ({"time_str": "19:45"}, "INVALID_INCREMENT"),
    ({"date_str": "2026-09-28"}, "PAST_TIME"),
    ({"date_str": "2026-11-29"}, "TOO_FAR_AHEAD"),
    ({"party_size": 9}, "PARTY_TOO_LARGE"),
    ({"party_size": 0}, "PARTY_TOO_SMALL"),
])
def test_tc_3_2_04_new_values_must_satisfy_every_rule(service, changes, code):
    """US-3 / AC-3.2 / TC-3.2.04 [failure] Business rules apply to the modified reservation too."""
    record = mine(service)
    assert rejects(service.modify, record["confirmation_code"], **changes).code == code
    assert service.get(record["confirmation_code"]) == record


def test_tc_3_2_05_moving_onto_a_date_with_another_booking_is_refused(service):
    """US-3 / AC-3.2 / TC-3.2.05 [failure] BR-8 applies when the date changes."""
    code = mine(service)["confirmation_code"]
    mine(service, date=SUNDAY)
    assert rejects(service.modify, code, date_str=SUNDAY).code == "DUPLICATE_GUEST_DATE"


def test_tc_3_2_06_no_actual_change_is_reported(service):
    """US-3 / AC-3.2 / TC-3.2.06 [edge]"""
    code = mine(service)["confirmation_code"]
    assert rejects(service.modify, code, time_str="19:00", party_size=4).code == "NO_CHANGES"


def test_tc_3_2_07_slot_taken_between_preview_and_yes(service):
    """US-3 / AC-3.2 / TC-3.2.07 [edge] Re-validated at commit; the original booking survives."""
    record = mine(service, time="17:00")
    service.create(SATURDAY, "20:00", 6, "Other Guest", "555-0001")
    change = modification(record["confirmation_code"], time="20:00", party_size=6)
    session = new_session()
    agent, llm = make_agent(service, call("modify_reservation", _id="preview", **change), say("OK?"),
                            call("modify_reservation", _id="confirm", **change), say("Sorry."))
    agent.handle(session, "Move to 8 for 6")
    service.create(SATURDAY, "20:00", 6, "Someone Faster", "555-0002")
    agent.handle(session, "Yes")
    assert result_of(llm, "confirm")["error_code"] == "SLOT_UNAVAILABLE"
    assert service.get(record["confirmation_code"]) == record


def test_tc_3_2_08_changing_the_request_needs_a_new_confirmation(service):
    """US-3 / AC-3.2 / TC-3.2.08 [edge] "Actually make it 8:30" after the 8:00 preview."""
    code = mine(service)["confirmation_code"]
    session = new_session()
    agent, _ = make_agent(
        service,
        call("modify_reservation", _id="p1", **modification(code, time="20:00")), say("8:00?"),
        call("modify_reservation", _id="p2", **modification(code, time="20:30")), say("8:30?"),
        call("modify_reservation", _id="c", **modification(code, time="20:30")), say("Done."),
    )
    agent.handle(session, "Move to 8")
    agent.handle(session, "Actually 8:30")
    assert service.get(code)["time"] == "19:00"
    agent.handle(session, "Yes")
    assert service.get(code)["time"] == "20:30"


# --- AC-3.3 Original slot is released; new slot is held ----------------------------------------------


def test_tc_3_3_01_old_slot_is_released_and_new_slot_is_held(service):
    """US-3 / AC-3.3 / TC-3.3.01 [happy]"""
    code = mine(service, party=6)["confirmation_code"]
    service.create(SATURDAY, "19:00", 6, "Other Guest", "555-0001")
    assert service.check_availability(SATURDAY, "19:00", 6)["available"] is False
    service.modify(code, time_str="21:00")
    assert service.check_availability(SATURDAY, "19:00", 6)["available"] is True   # released
    service.create(SATURDAY, "21:00", 6, "Third Guest", "555-0003")
    assert service.check_availability(SATURDAY, "21:00", 6)["available"] is False  # new slot held


def test_tc_3_3_02_shift_overlapping_its_own_hold_is_allowed(service):
    """US-3 / AC-3.3 / TC-3.3.02 [edge] A 30-minute move doesn't conflict with the booking itself."""
    code = mine(service, party=6)["confirmation_code"]
    service.create(SATURDAY, "19:00", 6, "Other Guest", "555-0001")
    assert service.modify(code, time_str="19:30")["time"] == "19:30"


def test_tc_3_3_03_shrinking_a_combined_party_frees_the_extra_table(service, db_path):
    """US-3 / AC-3.3 / TC-3.3.03 [edge]"""
    code = mine(service, party=8)["confirmation_code"]
    assert len(tables_of(db_path, code)) == 2
    service.modify(code, party_size=4)
    assert len(tables_of(db_path, code)) == 1


def test_tc_3_3_04_moving_to_another_date_releases_the_old_date(service):
    """US-3 / AC-3.3 / TC-3.3.04 [edge]"""
    code = mine(service, party=6)["confirmation_code"]
    service.create(SATURDAY, "19:00", 6, "Other Guest", "555-0001")
    service.modify(code, date_str=SUNDAY)
    assert service.check_availability(SATURDAY, "19:00", 6)["available"] is True
    assert service.get(code)["date"] == SUNDAY


def test_tc_3_3_05_seed_demo_modify_flow(service):
    """US-3 / AC-3.3 / TC-3.3.05 [happy] BV-DEMO 19:00 -> 20:00 frees 19:00 for others."""
    seed_demo_data(service, include_scenarios=False)
    service.modify("BV-DEMO", time_str="20:00")
    assert service.get("BV-DEMO")["time"] == "20:00"
    assert service.check_availability(DEMO_DAY, "19:00", 4)["available"] is True
