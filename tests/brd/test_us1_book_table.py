"""US-1 - Book a table (happy path).

AC-1.1 Agent collects: party size, date, time, guest name, phone number
AC-1.2 Agent confirms availability before promising the booking
AC-1.3 Agent returns a confirmation code the guest can reference later
AC-1.4 The reservation is persisted and visible in the data store
"""

import re
import threading

import pytest
from fastapi.testclient import TestClient

from app.agent import TOOLS, build_system_prompt
from app.main import create_app
from app.reservations import ReservationService
from app.rules import CODE_ALPHABET, ReservationError
from tests.brd.helpers import (
    MONDAY, RESTAURANT_PHONE, SATURDAY, FakeLLM, app_settings, booking, call, make_agent, new_session,
    preview_then_confirm, result_of, say,
)
from tests.conftest import NOW

CODE_RE = re.compile(rf"^BV-[{CODE_ALPHABET}]{{4}}$")


def rejects(fn, *args, **kwargs) -> ReservationError:
    with pytest.raises(ReservationError) as exc:
        fn(*args, **kwargs)
    return exc.value


# --- AC-1.1 Agent collects party size, date, time, guest name, phone -----------------------------


def test_tc_1_1_01_booking_tool_requires_all_five_details():
    """US-1 / AC-1.1 / TC-1.1.01 [happy] The create tool can't be called without the five required details."""
    schema = next(t for t in TOOLS if t["function"]["name"] == "create_reservation")["function"]["parameters"]
    assert {"date", "time", "party_size", "guest_name", "phone"} <= set(schema["required"])
    for field in ("date", "time", "guest_name", "phone"):
        assert schema["properties"][field]["type"] == "string"
    assert schema["properties"]["party_size"]["type"] == "integer"


def test_tc_1_1_02_collected_details_are_stored_exactly(service):
    """US-1 / AC-1.1 / TC-1.1.02 [happy] Details gathered in conversation are saved as given."""
    _, confirmed, _ = preview_then_confirm(service, "create_reservation", booking(party_size=3, phone="(555) 012-3456"))
    saved = service.get(confirmed["reservation"]["confirmation_code"])
    assert (saved["date"], saved["time"], saved["party_size"], saved["guest_name"], saved["phone"]) == \
        (SATURDAY, "19:00", 3, "Alex Rivera", "(555) 012-3456")


@pytest.mark.parametrize("name", ["", "   ", "N/A", "1234", "x", "<b>guest</b>"])
def test_tc_1_1_03_missing_or_placeholder_name_is_rejected(service, name):
    """US-1 / AC-1.1 / TC-1.1.03 [failure] The model can't fill the name with a placeholder."""
    assert rejects(service.create, SATURDAY, "19:00", 2, name, "555-0199").code == "INVALID_NAME"


@pytest.mark.parametrize("phone", ["", "unknown", "n/a", "123", "555-01x9", "1" * 16])
def test_tc_1_1_04_missing_or_placeholder_phone_is_rejected(service, phone):
    """US-1 / AC-1.1 / TC-1.1.04 [failure] The model can't fill the phone with a placeholder."""
    assert rejects(service.create, SATURDAY, "19:00", 2, "Alex Rivera", phone).code == "INVALID_PHONE"


@pytest.mark.parametrize("party, code", [(0, "PARTY_TOO_SMALL"), (-2, "PARTY_TOO_SMALL"), (9, "PARTY_TOO_LARGE"),
                                         (12, "PARTY_TOO_LARGE")])
def test_tc_1_1_05_party_size_outside_1_to_8_is_rejected(service, party, code):
    """US-1 / AC-1.1 / TC-1.1.05 [failure] BR-3/BR-4 party-size limits."""
    assert rejects(service.check_availability, SATURDAY, "19:00", party).code == code


@pytest.mark.parametrize("name, phone", [("José García", "+44 20 7946 0958"), ("O'Brien", "555.012.3456"),
                                         ("Mary-Kate Smith", "(555) 012-3456"), ("Cher", "5550123")])
def test_tc_1_1_06_real_world_names_and_phone_formats_are_accepted(service, name, phone):
    """US-1 / AC-1.1 / TC-1.1.06 [edge] Accents, apostrophes, hyphens, single names, international phones."""
    assert service.create(SATURDAY, "19:00", 2, name, phone)["guest_name"] == name


def test_tc_1_1_07_prompt_tells_agent_to_ask_and_never_invent():
    """US-1 / AC-1.1 / TC-1.1.07 [prompt-level] Collection behavior is instructed (live check: L-1)."""
    prompt = build_system_prompt(NOW, RESTAURANT_PHONE)
    assert "party size, date, time, guest name, and phone number" in prompt
    assert "Never invent or guess guest details" in prompt


# --- AC-1.2 Agent confirms availability before promising the booking --------------------------


def test_tc_1_2_01_open_slot_reports_available(service):
    """US-1 / AC-1.2 / TC-1.2.01 [happy]"""
    result = service.check_availability(SATURDAY, "19:00", 4)
    assert result["available"] is True and result["when"] == "Saturday, October 3 at 7:00 PM"


def test_tc_1_2_02_first_create_call_is_a_preview_that_saves_nothing(service):
    """US-1 / AC-1.2 / TC-1.2.02 [happy] Nothing is promised or saved until the guest confirms."""
    session = new_session()
    agent, llm = make_agent(service, call("create_reservation", **booking()), say("Shall I book it?"))
    agent.handle(session, "Book it")
    result = result_of(llm, "call_1")
    assert result["error_code"] == "CONFIRMATION_REQUIRED" and "confirmation_code" not in result["preview"]
    assert service.list_for_date(SATURDAY) == []


def test_tc_1_2_03_full_slot_is_never_previewed_as_bookable(service):
    """US-1 / AC-1.2 / TC-1.2.03 [failure]"""
    for phone in ("555-0001", "555-0002"):
        service.create(SATURDAY, "19:00", 6, "Other Guest", phone)
    session = new_session()
    agent, llm = make_agent(service, call("create_reservation", **booking(party_size=6)), say("Sorry, full."))
    agent.handle(session, "6 at 7pm")
    assert result_of(llm, "call_1")["error_code"] == "SLOT_UNAVAILABLE"
    assert session.pending is None


@pytest.mark.parametrize("date, time, code", [
    (MONDAY, "19:00", "CLOSED_DAY"),
    (SATURDAY, "16:30", "OUTSIDE_HOURS"),
    (SATURDAY, "22:00", "OUTSIDE_HOURS"),
    (SATURDAY, "19:15", "INVALID_INCREMENT"),
    ("2026-09-28", "19:00", "PAST_TIME"),
    ("2026-09-29", "11:00", "OUTSIDE_HOURS"),
    ("2026-11-29", "19:00", "TOO_FAR_AHEAD"),
    ("2026-02-30", "19:00", "INVALID_DATE"),
    (SATURDAY, "7pm", "INVALID_TIME"),
])
def test_tc_1_2_04_rule_violations_are_caught_before_any_promise(service, date, time, code):
    """US-1 / AC-1.2 / TC-1.2.04 [failure] BR-1, BR-2, BR-5, BR-6 and input formats."""
    assert rejects(service.check_availability, date, time, 2).code == code
    assert rejects(service.create, date, time, 2, "Alex Rivera", "555-0199", dry_run=True).code == code


def test_tc_1_2_05_slot_taken_between_preview_and_yes(service):
    """US-1 / AC-1.2 / TC-1.2.05 [edge] Availability is re-checked when the guest confirms."""
    service.create(SATURDAY, "19:00", 6, "Other Guest", "555-0001")
    session = new_session()
    agent, llm = make_agent(
        service,
        call("create_reservation", _id="preview", **booking(party_size=6)), say("Shall I book it?"),
        call("create_reservation", _id="confirm", **booking(party_size=6)), say("Sorry, it just filled."),
    )
    agent.handle(session, "6 at 7pm")
    service.create(SATURDAY, "19:00", 6, "Someone Faster", "555-0002")  # takes the last 6-top
    agent.handle(session, "Yes")
    result = result_of(llm, "confirm")
    assert result["error_code"] == "SLOT_UNAVAILABLE" and "alternatives" in result
    assert [r["guest_name"] for r in service.list_for_date(SATURDAY)] == ["Other Guest", "Someone Faster"]


def test_tc_1_2_06_slot_passes_between_preview_and_yes(service, clock):
    """US-1 / AC-1.2 / TC-1.2.06 [edge] A same-day slot that starts before the guest says yes is refused."""
    clock.now = NOW.replace(hour=16, minute=55)
    session = new_session()
    agent, llm = make_agent(
        service,
        call("create_reservation", _id="preview", **booking(date="2026-09-29", time="17:00")), say("OK?"),
        call("create_reservation", _id="confirm", **booking(date="2026-09-29", time="17:00")), say("Too late."),
    )
    agent.handle(session, "17:00 today")
    clock.now = NOW.replace(hour=17, minute=1)
    agent.handle(session, "Yes")
    assert result_of(llm, "confirm")["error_code"] == "PAST_TIME"
    assert service.list_for_date("2026-09-29") == []


@pytest.mark.parametrize("date, time", [("2026-09-29", "17:00"), (SATURDAY, "21:30"), ("2026-11-28", "17:00"),
                                        ("2026-10-04", "19:00")])
def test_tc_1_2_07_boundary_slots_are_bookable(service, date, time):
    """US-1 / AC-1.2 / TC-1.2.07 [edge] Later today, last seating, day 60, Sunday."""
    assert service.check_availability(date, time, 2)["available"] is True


# --- AC-1.3 Agent returns a confirmation code --------------------------------------------------------


def test_tc_1_3_01_confirmed_booking_returns_readable_code(service):
    """US-1 / AC-1.3 / TC-1.3.01 [happy] BR-9 format."""
    _, confirmed, _ = preview_then_confirm(service, "create_reservation", booking())
    assert CODE_RE.match(confirmed["reservation"]["confirmation_code"])
    assert confirmed["reservation"]["when"] == "Saturday, October 3 at 7:00 PM"


@pytest.mark.parametrize("typed", ["{code}", "{lower}", "{spaced}", "{bare}"])
def test_tc_1_3_02_code_can_be_referenced_later_in_any_format(service, typed):
    """US-1 / AC-1.3 / TC-1.3.02 [edge] Lower case, spaces, or no BV- prefix still find the booking."""
    code = service.create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199")["confirmation_code"]
    variant = typed.format(code=code, lower=code.lower(), spaced=code.replace("-", " "), bare=code[3:])
    assert service.get(variant)["confirmation_code"] == code


def test_tc_1_3_03_codes_are_unique_across_many_bookings(service):
    """US-1 / AC-1.3 / TC-1.3.03 [edge] BR-9 uniqueness."""
    codes = set()
    for day in ("2026-10-01", "2026-10-02", SATURDAY, "2026-10-04", "2026-10-06"):
        for i, time in enumerate(["17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00",
                                  "21:30"]):
            codes.add(service.create(day, time, 2, "Guest Name", f"555-3{i:03d}")["confirmation_code"])
    assert len(codes) == 50 and all(CODE_RE.match(c) for c in codes)


def test_tc_1_3_04_code_collision_is_retried(db_path, clock):
    """US-1 / AC-1.3 / TC-1.3.04 [failure] A generated duplicate code never reaches the guest."""
    codes = iter(["BV-AAAA", "BV-AAAA", "BV-BBBB"])
    svc = ReservationService(db_path, clock, code_factory=lambda: next(codes))
    svc.create(SATURDAY, "19:00", 2, "First Guest", "555-0001")
    assert svc.create(SATURDAY, "19:00", 2, "Second Guest", "555-0002")["confirmation_code"] == "BV-BBBB"


def test_tc_1_3_05_code_is_given_even_if_the_model_fails_after_saving(service):
    """US-1 / AC-1.3 / TC-1.3.05 [failure] The guest still gets their code when the final LLM call fails."""
    session = new_session()
    agent, _ = make_agent(
        service,
        call("create_reservation", _id="preview", **booking()), say("Shall I book it?"),
        call("create_reservation", _id="confirm", **booking()), RuntimeError("provider timeout"),
    )
    agent.handle(session, "Book it")
    reply = agent.handle(session, "Yes")
    [saved] = service.list_for_date(SATURDAY)
    assert saved["confirmation_code"] in reply and "Saturday, October 3 at 7:00 PM" in reply


# --- AC-1.4 The reservation is persisted and visible in the data store ------------------------------


def test_tc_1_4_01_persisted_record_matches_brd_schema(service):
    """US-1 / AC-1.4 / TC-1.4.01 [happy] BRD §9 structure and types."""
    _, confirmed, _ = preview_then_confirm(service, "create_reservation", booking(notes="anniversary"))
    saved = service.get(confirmed["reservation"]["confirmation_code"])
    assert set(saved) == {"confirmation_code", "date", "time", "party_size", "guest_name", "phone", "notes",
                          "status", "created_at"}
    assert saved["status"] == "confirmed" and isinstance(saved["party_size"], int)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", saved["created_at"])


def test_tc_1_4_02_booking_made_over_http_is_visible_in_the_data_store(tmp_path):
    """US-1 / AC-1.4 / TC-1.4.02 [happy] End to end through /api/chat, then /api/reservations."""
    llm = FakeLLM(call("create_reservation", _id="p", **booking()), say("Shall I book it?"),
                  call("create_reservation", _id="c", **booking()), say("Booked!"))
    client = TestClient(create_app(app_settings(tmp_path / "a.db"), complete=llm, clock=lambda: NOW))
    first = client.post("/api/chat", json={"message": "4 on Saturday at 7, Alex Rivera 555-0199"}).json()
    client.post("/api/chat", json={"session_id": first["session_id"], "message": "Yes"})
    rows = client.get("/api/reservations", params={"date": SATURDAY}).json()
    assert [(r["guest_name"], r["time"], r["status"]) for r in rows] == [("Alex Rivera", "19:00", "confirmed")]


def test_tc_1_4_03_reservations_survive_a_server_restart(tmp_path):
    """US-1 / AC-1.4 / TC-1.4.03 [edge] Data persists to disk; the seed is not applied twice."""
    path = tmp_path / "restart.db"
    first = TestClient(create_app(app_settings(path), complete=None, clock=lambda: NOW))
    ReservationService(path, lambda: NOW).create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199")
    del first
    second = TestClient(create_app(app_settings(path), complete=None, clock=lambda: NOW))
    assert len(second.get("/api/reservations", params={"date": SATURDAY}).json()) == 1
    assert [r["confirmation_code"] for r in second.get("/api/reservations", params={"date": "2026-09-30"}).json()
            ].count("BV-DEMO") == 1


def test_tc_1_4_04_concurrent_guests_never_double_book(db_path, clock):
    """US-1 / AC-1.4 / TC-1.4.04 [failure] BO-2: 20 simultaneous requests for two 6-tops."""
    barrier, outcomes = threading.Barrier(20), []

    def attempt(i):
        svc = ReservationService(db_path, clock)
        barrier.wait()
        try:
            svc.create(SATURDAY, "19:00", 6, "Racing Guest", f"555-40{i:02d}")
            outcomes.append("ok")
        except ReservationError as err:
            outcomes.append(err.code)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 2 and outcomes.count("SLOT_UNAVAILABLE") == 18


def test_tc_1_4_05_one_reservation_per_guest_per_date(service):
    """US-1 / AC-1.4 / TC-1.4.05 [failure] BR-8, matching phone numbers regardless of formatting."""
    service.create(SATURDAY, "17:00", 2, "Alex Rivera", "555-0199")
    assert rejects(service.create, SATURDAY, "21:00", 2, "A. Rivera", "(555) 0199").code == "DUPLICATE_GUEST_DATE"
    assert service.create("2026-10-04", "19:00", 2, "Alex Rivera", "555-0199")["status"] == "confirmed"
