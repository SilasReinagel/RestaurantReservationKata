"""US-4 - Cancel a reservation.

AC-4.1 Guest provides confirmation code (or name + date; decision D-4: code only in this version)
AC-4.2 Agent confirms the cancellation before executing
AC-4.3 Slot becomes available again
"""

import pytest

from app.agent import LLMUnavailable
from app.reservations import seed_demo_data
from app.rules import ReservationError
from tests.brd.helpers import SATURDAY, call, make_agent, new_session, result_of, say
from tests.conftest import NOW


def rejects(fn, *args, **kwargs) -> ReservationError:
    with pytest.raises(ReservationError) as exc:
        fn(*args, **kwargs)
    return exc.value


def lookup_then_cancel(service, code: str, typed: str | None = None):
    """Turn 1: look up and read back. Turn 2: guest says yes, agent cancels."""
    session = new_session()
    agent, llm = make_agent(
        service,
        call("get_reservation", _id="lookup", confirmation_code=typed or code),
        say("That's a reservation for Alex Rivera, party of 4. Cancel it?"),
        call("cancel_reservation", _id="cancel", confirmation_code=typed or code), say("Cancelled."),
    )
    agent.handle(session, f"I need to cancel {typed or code}.")
    agent.handle(session, "Yes.")
    return result_of(llm, "lookup"), result_of(llm, "cancel")


# --- AC-4.1 Guest provides the confirmation code ---------------------------------------------------


def test_tc_4_1_01_brd_example_10_3_cancel_bv_demo(service):
    """US-4 / AC-4.1 / TC-4.1.01 [happy] "I need to cancel BV-DEMO." -> read back -> "Yes." -> cancelled."""
    seed_demo_data(service, include_scenarios=False)
    lookup, cancelled = lookup_then_cancel(service, "BV-DEMO")
    assert (lookup["reservation"]["guest_name"], lookup["reservation"]["party_size"]) == ("Alex Rivera", 4)
    assert lookup["reservation"]["when"] == "Wednesday, September 30 at 7:00 PM"
    assert cancelled["ok"] and service.get("BV-DEMO")["status"] == "cancelled"


@pytest.mark.parametrize("typed", ["bv-demo", "BV DEMO", " BV-DEMO. "])
def test_tc_4_1_02_code_in_any_format(service, typed):
    """US-4 / AC-4.1 / TC-4.1.02 [edge]"""
    seed_demo_data(service, include_scenarios=False)
    _, cancelled = lookup_then_cancel(service, "BV-DEMO", typed)
    assert cancelled["ok"]


def test_tc_4_1_03_unknown_code_is_reported(service):
    """US-4 / AC-4.1 / TC-4.1.03 [failure]"""
    agent, llm = make_agent(service, call("get_reservation", confirmation_code="BV-ZZZZ"), say("Not found."))
    agent.handle(new_session(), "Cancel BV-ZZZZ")
    assert result_of(llm, "call_1")["error_code"] == "NOT_FOUND"


# --- AC-4.2 Agent confirms the cancellation before executing ------------------------------------------


def test_tc_4_2_01_cancel_without_lookup_is_refused(service):
    """US-4 / AC-4.2 / TC-4.2.01 [failure] The model can't skip reading the reservation back."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    agent, llm = make_agent(service, call("cancel_reservation", confirmation_code=code), say("..."))
    agent.handle(new_session(), f"Cancel {code} now")
    assert result_of(llm, "call_1")["error_code"] == "CONFIRMATION_REQUIRED"
    assert service.get(code)["status"] == "confirmed"


def test_tc_4_2_02_cancel_in_the_same_turn_as_the_lookup_is_refused(service):
    """US-4 / AC-4.2 / TC-4.2.02 [failure] The guest must answer after seeing the details."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    agent, llm = make_agent(service, call("get_reservation", _id="g", confirmation_code=code),
                            call("cancel_reservation", _id="c", confirmation_code=code), say("..."))
    agent.handle(new_session(), f"Cancel {code}")
    assert result_of(llm, "c")["error_code"] == "CONFIRMATION_REQUIRED"
    assert service.get(code)["status"] == "confirmed"


def test_tc_4_2_03_guest_declines_and_nothing_changes(service):
    """US-4 / AC-4.2 / TC-4.2.03 [edge] "No, keep it" -> no cancel call -> still confirmed."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    session = new_session()
    agent, _ = make_agent(service, call("get_reservation", confirmation_code=code), say("Cancel it?"),
                          say("No problem, it stays booked."))
    agent.handle(session, f"Cancel {code}")
    agent.handle(session, "No, keep it")
    assert service.get(code)["status"] == "confirmed"


def test_tc_4_2_04_lookup_the_guest_never_saw_does_not_count_as_confirmation(service):
    """US-4 / AC-4.2 / TC-4.2.04 [edge] The lookup happens, but the reply that reads it back never reaches
    the guest (LLM outage). On the retry, the model cancels straight away. The guest never saw the
    details or said yes, so the cancel must be refused. The normal flow must still work afterwards."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    session = new_session()
    agent, llm = make_agent(
        service,
        # Turn 1: lookup succeeds, then the model call that would read it back fails.
        call("get_reservation", _id="g", confirmation_code=code), RuntimeError("provider timeout"),
        # Turn 2: guest retries; the model tries to cancel at once -> refused; it looks up and reads back.
        call("cancel_reservation", _id="c", confirmation_code=code),
        call("get_reservation", _id="g2", confirmation_code=code), say("That's Alex Rivera, party of 4. Cancel it?"),
        # Turn 3: guest says yes -> cancelled.
        call("cancel_reservation", _id="c2", confirmation_code=code), say("Cancelled."),
    )
    with pytest.raises(LLMUnavailable):
        agent.handle(session, f"Cancel {code}")
    assert session.looked_up == {}  # the unseen lookup no longer authorizes anything
    agent.handle(session, f"Cancel {code}")  # guest retries the same request; no read-back, no "yes"
    assert result_of(llm, "c")["error_code"] == "CONFIRMATION_REQUIRED"
    assert service.get(code)["status"] == "confirmed"
    agent.handle(session, "Yes")
    assert result_of(llm, "c2")["ok"] and service.get(code)["status"] == "cancelled"


def test_tc_4_2_05_already_cancelled_is_reported(service):
    """US-4 / AC-4.2 / TC-4.2.05 [failure]"""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    service.cancel(code)
    assert rejects(service.cancel, code).code == "ALREADY_CANCELLED"


def test_tc_4_2_06_past_reservation_cannot_be_cancelled(service, clock):
    """US-4 / AC-4.2 / TC-4.2.06 [failure]"""
    code = service.create("2026-09-29", "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    clock.now = NOW.replace(hour=20)
    assert rejects(service.cancel, code).code == "RESERVATION_IN_PAST"


# --- AC-4.3 Slot becomes available again ----------------------------------------------------------------


def test_tc_4_3_01_cancelled_slot_can_be_booked_by_someone_else(service):
    """US-4 / AC-4.3 / TC-4.3.01 [happy]"""
    first = service.create(SATURDAY, "19:00", 6, "Alex Rivera", "555-0199")
    service.create(SATURDAY, "19:00", 6, "Other Guest", "555-0001")
    assert service.check_availability(SATURDAY, "19:00", 6)["available"] is False
    service.cancel(first["confirmation_code"])
    assert service.check_availability(SATURDAY, "19:00", 6)["available"] is True
    assert service.create(SATURDAY, "19:00", 6, "New Guest", "555-0002")["status"] == "confirmed"


def test_tc_4_3_02_cancelled_record_is_kept_with_status_cancelled(service):
    """US-4 / AC-4.3 / TC-4.3.02 [edge] BRD §9 status field; the record isn't deleted."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    service.cancel(code)
    [row] = service.list_for_date(SATURDAY)
    assert row["confirmation_code"] == code and row["status"] == "cancelled"


def test_tc_4_3_03_same_guest_can_rebook_the_same_date(service):
    """US-4 / AC-4.3 / TC-4.3.03 [edge] BR-8 ignores cancelled reservations."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    service.cancel(code)
    assert service.create(SATURDAY, "20:00", 4, "Alex Rivera", "555-0199")["status"] == "confirmed"


def test_tc_4_3_04_cancelling_a_combined_party_frees_both_tables(service):
    """US-4 / AC-4.3 / TC-4.3.04 [edge] After five parties of 8 a sixth can't fit; cancelling one frees a 4+4 pair."""
    codes = [service.create(SATURDAY, "19:00", 8, "Big Party", f"555-05{i:02d}")["confirmation_code"]
             for i in range(5)]
    assert service.check_availability(SATURDAY, "19:00", 8)["available"] is False
    service.cancel(codes[0])
    assert service.check_availability(SATURDAY, "19:00", 8)["available"] is True
