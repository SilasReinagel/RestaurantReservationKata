"""US-5 - Handle special requests.

AC-5.1 Free-text notes are captured and stored with the reservation
AC-5.2 Agent does not promise accommodations it cannot guarantee
"""

import pytest

from app.agent import TOOLS, build_system_prompt
from app.rules import ReservationError
from tests.brd.helpers import (
    RESTAURANT_PHONE, SATURDAY, booking, call, make_agent, modification, new_session, preview_then_confirm,
    say, tables_of,
)
from tests.conftest import NOW


# --- AC-5.1 Notes are captured and stored ------------------------------------------------------------


@pytest.mark.parametrize("notes", ["anniversary, prefers quiet table", "vegetarian x2", "severe peanut allergy",
                                   "wheelchair access", "window seat if possible"])
def test_tc_5_1_01_special_request_is_stored_with_the_booking(service, notes):
    """US-5 / AC-5.1 / TC-5.1.01 [happy] Dietary needs, occasions, seating and access preferences."""
    _, confirmed, _ = preview_then_confirm(service, "create_reservation", booking(notes=notes))
    assert service.get(confirmed["reservation"]["confirmation_code"])["notes"] == notes


def test_tc_5_1_02_notes_are_shown_in_the_preview_the_guest_confirms(service):
    """US-5 / AC-5.1 / TC-5.1.02 [happy] The guest sees the note before confirming."""
    preview, _, _ = preview_then_confirm(service, "create_reservation", booking(notes="birthday"))
    assert preview["preview"]["notes"] == "birthday"


@pytest.mark.parametrize("given, stored", [(None, ""), ("", ""), ("   birthday  ", "birthday")])
def test_tc_5_1_03_missing_or_padded_notes_are_normalized(service, given, stored):
    """US-5 / AC-5.1 / TC-5.1.03 [edge]"""
    assert service.create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199", given)["notes"] == stored


def test_tc_5_1_04_notes_length_limit(service):
    """US-5 / AC-5.1 / TC-5.1.04 [edge/failure] 500 characters accepted, 501 rejected."""
    assert len(service.create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199", "x" * 500)["notes"]) == 500
    with pytest.raises(ReservationError) as exc:
        service.create(SATURDAY, "19:00", 2, "Sam Lee", "555-0198", "x" * 501)
    assert exc.value.code == "NOTES_TOO_LONG"


def test_tc_5_1_05_notes_are_stored_verbatim(service):
    """US-5 / AC-5.1 / TC-5.1.05 [edge] Unicode, emoji and markup are kept as data (the UI escapes on display)."""
    text = "Café 🎂 <b>nut-free</b> dessert, \"surprise\" & candles"
    assert service.create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199", text)["notes"] == text


def test_tc_5_1_06_reworded_note_on_confirmation_is_saved_without_asking_again(service):
    """US-5 / AC-5.1 / TC-5.1.06 [edge] Notes aren't part of the confirmation match."""
    session = new_session()
    agent, _ = make_agent(
        service,
        call("create_reservation", _id="p", **booking(notes="anniversary")), say("OK?"),
        call("create_reservation", _id="c", **booking(notes="Anniversary dinner")), say("Booked."),
    )
    agent.handle(session, "Book it, it's our anniversary")
    agent.handle(session, "Yes")
    [saved] = service.list_for_date(SATURDAY)
    assert saved["notes"] == "Anniversary dinner"


def test_tc_5_1_07_notes_can_be_added_later_by_modifying(service):
    """US-5 / AC-5.1 / TC-5.1.07 [happy] Notes-only change; the slot stays the same."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199")["confirmation_code"]
    preview_then_confirm(service, "modify_reservation", modification(code, notes="high chair needed"))
    saved = service.get(code)
    assert (saved["notes"], saved["time"], saved["party_size"]) == ("high chair needed", "19:00", 4)


def test_tc_5_1_08_notes_can_be_cleared(service):
    """US-5 / AC-5.1 / TC-5.1.08 [edge] An empty string clears; null means unchanged."""
    code = service.create(SATURDAY, "19:00", 4, "Alex Rivera", "555-0199", "birthday")["confirmation_code"]
    assert service.modify(code, notes="")["notes"] == ""


# --- AC-5.2 Never promise accommodations -----------------------------------------------------------


def test_tc_5_2_01_seating_notes_do_not_change_table_assignment(service, db_path):
    """US-5 / AC-5.2 / TC-5.2.01 [edge] A "window seat" note can't reserve a specific table: nothing
    guarantees it, so the agent has nothing it could truthfully promise."""
    plain = service.create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199")
    service.cancel(plain["confirmation_code"])
    window = service.create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199", "window seat please")
    assert tables_of(db_path, window["confirmation_code"]) == tables_of(db_path, plain["confirmation_code"])


def test_tc_5_2_02_no_tool_can_guarantee_an_accommodation():
    """US-5 / AC-5.2 / TC-5.2.02 [edge] Notes are the only way to record a request."""
    properties = set()
    for tool in TOOLS:
        properties |= set(tool["function"]["parameters"]["properties"])
    assert not {"table", "table_id", "seating", "section"} & properties


def test_tc_5_2_03_prompt_forbids_promising_accommodations():
    """US-5 / AC-5.2 / TC-5.2.03 [prompt-level] "I'll note that", never "you'll get". Live check: L-6."""
    prompt = build_system_prompt(NOW, RESTAURANT_PHONE)
    assert "Say you'll note them; never promise they will be accommodated" in prompt
