"""US-2 - Suggest alternatives when unavailable.

AC-2.1 When the requested slot is unavailable, the agent offers at least two alternative times within
       ±90 minutes on the same day (amended by decision D-7: offer only the real ones, even if fewer
       than two, or none)
AC-2.2 The agent does not invent slots that are not actually available
"""

import pytest

from app.agent import build_system_prompt
from app.reservations import seed_demo_data
from app.rules import ReservationError, parse_time
from tests.brd.helpers import DEMO_DAY, RESTAURANT_PHONE, SATURDAY, booking, call, make_agent, new_session, \
    result_of, say
from tests.conftest import NOW


def six_tops_booked_at(service, t6_1, t6_2=None, date=SATURDAY):
    """Hold T6-1 from t6_1 and T6-2 from t6_2 (both at t6_1 if t6_2 is omitted).

    The allocator always picks the lowest free id, so non-overlapping bookings would both land on
    T6-1. To put one on each table, a temporary booking occupies T6-1 while the T6-2 one is made.
    """
    t6_2 = t6_2 or t6_1
    temp = service.create(date, t6_2, 6, "Temp Holder", "555-0098")        # -> T6-1
    service.create(date, t6_2, 6, "Other Guest", "555-0002")              # -> T6-2
    service.cancel(temp["confirmation_code"])
    service.create(date, t6_1, 6, "Other Guest", "555-0001")              # -> T6-1


def within_90(requested: str, alternatives: list[str]) -> bool:
    return all(abs(parse_time(a) - parse_time(requested)) <= 90 for a in alternatives)


# --- AC-2.1 Offer alternatives within ±90 minutes on the same day ----------------------------------


def test_tc_2_1_01_brd_example_10_2_offers_real_nearby_times(service):
    """US-2 / AC-2.1 / TC-2.1.01 [happy] "Table for 6 tomorrow at 8pm?" -> full, offer 9:00pm / 6:30pm."""
    seed_demo_data(service, include_scenarios=True)
    result = service.check_availability(DEMO_DAY, "20:00", 6)
    assert result["available"] is False
    assert result["alternatives"] == ["21:00", "18:30", "21:30"]
    assert len(result["alternatives"]) >= 2 and within_90("20:00", result["alternatives"])


def test_tc_2_1_02_alternatives_are_closest_first_and_capped_at_four(service):
    """US-2 / AC-2.1 / TC-2.1.02 [happy] T6-1 held from 20:30, T6-2 held until 19:30 -> six real options."""
    six_tops_booked_at(service, "20:30", "17:30")
    result = service.check_availability(SATURDAY, "19:00", 6)
    assert result["available"] is False
    assert result["alternatives"] == ["18:30", "19:30", "18:00", "20:00"]  # ties go to the earlier time


def test_tc_2_1_03_only_one_real_alternative_is_offered_alone(service):
    """US-2 / AC-2.1 / TC-2.1.03 [edge] D-7: fewer than two real options -> show just those."""
    six_tops_booked_at(service, "19:00")
    assert service.check_availability(SATURDAY, "19:30", 6)["alternatives"] == ["21:00"]


def test_tc_2_1_04_no_real_alternative_returns_empty_list(service):
    """US-2 / AC-2.1 / TC-2.1.04 [edge] D-7: nothing within ±90 -> an empty list, never padding."""
    six_tops_booked_at(service, "20:00")
    result = service.check_availability(SATURDAY, "20:00", 6)
    assert result["available"] is False and result["alternatives"] == []


def test_tc_2_1_05_alternatives_stay_inside_opening_hours(service):
    """US-2 / AC-2.1 / TC-2.1.05 [edge] Never before 17:00 or after last seating 21:30."""
    with pytest.raises(ReservationError) as late:
        service.check_availability(SATURDAY, "22:30", 2)
    assert late.value.details["alternatives"] == ["21:30", "21:00"]
    with pytest.raises(ReservationError) as early:
        service.check_availability(SATURDAY, "16:30", 2)
    assert early.value.details["alternatives"] == ["17:00", "17:30", "18:00"]


def test_tc_2_1_06_alternatives_are_never_in_the_past(service, clock):
    """US-2 / AC-2.1 / TC-2.1.06 [edge] 17:30 would be free, but it's 18:10 now."""
    six_tops_booked_at(service, "19:30", date="2026-09-29")
    clock.now = NOW.replace(hour=18, minute=10)
    result = service.check_availability("2026-09-29", "19:00", 6)
    assert result["available"] is False and result["alternatives"] == []


def test_tc_2_1_07_off_grid_time_suggests_nearest_valid_times(service):
    """US-2 / AC-2.1 / TC-2.1.07 [edge] "7:15" -> suggest 7:00 and 7:30."""
    with pytest.raises(ReservationError) as exc:
        service.check_availability(SATURDAY, "19:15", 2)
    assert exc.value.code == "INVALID_INCREMENT" and exc.value.details["alternatives"][:2] == ["19:00", "19:30"]


def test_tc_2_1_08_failed_booking_hands_alternatives_to_the_agent(service):
    """US-2 / AC-2.1 / TC-2.1.08 [failure] The create/preview error carries the alternatives."""
    six_tops_booked_at(service, "19:00")
    agent, llm = make_agent(service, call("create_reservation", **booking(time="19:30", party_size=6)),
                            say("That's full; 9pm is open."))
    agent.handle(new_session(), "6 at 7:30")
    result = result_of(llm, "call_1")
    assert result["error_code"] == "SLOT_UNAVAILABLE" and result["alternatives"] == ["21:00"]


def test_tc_2_1_09_alternatives_are_for_the_requested_party_size(service):
    """US-2 / AC-2.1 / TC-2.1.09 [edge] Full for 6 doesn't mean full for 4."""
    six_tops_booked_at(service, "19:00")
    assert service.check_availability(SATURDAY, "19:00", 6)["available"] is False
    assert service.check_availability(SATURDAY, "19:00", 4)["available"] is True


# --- AC-2.2 Never invent slots -----------------------------------------------------------------------


def test_tc_2_2_01_every_offered_alternative_is_actually_bookable(service):
    """US-2 / AC-2.2 / TC-2.2.01 [happy] Each alternative passes a real availability check and dry-run booking."""
    six_tops_booked_at(service, "20:30", "17:30")
    alternatives = service.check_availability(SATURDAY, "19:00", 6)["alternatives"]
    for time in alternatives:
        assert service.check_availability(SATURDAY, time, 6)["available"] is True
        service.create(SATURDAY, time, 6, "Probe Guest", "555-0999", dry_run=True)


def test_tc_2_2_02_alternative_must_be_free_for_the_full_two_hours(service):
    """US-2 / AC-2.2 / TC-2.2.02 [edge] 20:30 isn't offered: no booking starts then, but 19:00 holds run to 21:00."""
    six_tops_booked_at(service, "19:00")
    alternatives = service.check_availability(SATURDAY, "20:00", 6)["alternatives"]
    assert alternatives == ["21:00", "21:30"] and "20:30" not in alternatives


def test_tc_2_2_03_available_slot_returns_no_alternatives(service):
    """US-2 / AC-2.2 / TC-2.2.03 [edge] Nothing extra is suggested when the request itself is fine."""
    assert "alternatives" not in service.check_availability(SATURDAY, "19:00", 2)


def test_tc_2_2_04_booking_an_offered_alternative_succeeds(service):
    """US-2 / AC-2.2 / TC-2.2.04 [happy] Accepting a suggestion really books it."""
    seed_demo_data(service, include_scenarios=True)
    first = service.check_availability(DEMO_DAY, "20:00", 6)["alternatives"][0]
    assert service.create(DEMO_DAY, first, 6, "Pat Guest", "555-0777")["time"] == "21:00"


def test_tc_2_2_05_prompt_forbids_offering_times_not_returned_by_tools():
    """US-2 / AC-2.2 / TC-2.2.05 [prompt-level] Live check: L-2."""
    prompt = build_system_prompt(NOW, RESTAURANT_PHONE)
    assert "never offer a time" in prompt
    assert "If there are fewer than two alternatives, offer only what exists" in prompt
