"""US-6 - Refuse out-of-scope requests.

AC-6.1 Agent politely declines and redirects when asked about menu prices, ordering food, allergens
       beyond noting them, or unrelated topics
AC-6.2 Agent suggests calling the restaurant for questions outside its scope
"""

import pytest
from fastapi.testclient import TestClient

from app.agent import MAX_TOOL_ROUNDS, TOOLS, build_system_prompt
from app.main import create_app
from app.rules import ReservationError
from tests.brd.helpers import (
    RESTAURANT_PHONE, SATURDAY, app_settings, call, make_agent, new_session, result_of, say,
)
from tests.conftest import NOW


# --- AC-6.1 Politely decline menu prices, food orders, allergen advice, unrelated topics ------------


def test_tc_6_1_01_agent_has_only_reservation_tools():
    """US-6 / AC-6.1 / TC-6.1.01 [happy] There's no tool for menus, prices or orders, so no answer to make up."""
    assert {t["function"]["name"] for t in TOOLS} == {
        "check_availability", "create_reservation", "get_reservation", "modify_reservation", "cancel_reservation"
    }


@pytest.mark.parametrize("phrase", ["menu items, prices, taking food orders",
                                    "allergen or ingredient advice (you may only note an allergy)",
                                    "anything unrelated to reservations", "Politely decline"])
def test_tc_6_1_02_prompt_defines_the_out_of_scope_topics(phrase):
    """US-6 / AC-6.1 / TC-6.1.02 [prompt-level] Live checks: L-4, L-5, L-8."""
    assert phrase in build_system_prompt(NOW, RESTAURANT_PHONE)


@pytest.mark.parametrize("tool", ["get_menu", "place_order", "list_reservations"])
def test_tc_6_1_03_invented_tool_calls_are_refused_safely(service, tool):
    """US-6 / AC-6.1 / TC-6.1.03 [failure] If the model invents a tool, it gets an error, not a crash."""
    agent, llm = make_agent(service, call(tool), say("Sorry, I can only help with reservations."))
    agent.handle(new_session(), "What's on the menu?")
    assert result_of(llm, "call_1")["error_code"] == "UNKNOWN_TOOL"


@pytest.mark.parametrize("probe", ["%", "BV-%", "*", "' OR 1=1 --", ""])
def test_tc_6_1_04_lookup_cannot_be_abused_to_list_other_guests(service, probe):
    """US-6 / AC-6.1 / TC-6.1.04 [failure] Wildcards and injection strings find nothing."""
    service.create(SATURDAY, "19:00", 2, "Alex Rivera", "555-0199")
    with pytest.raises(ReservationError) as exc:
        service.get(probe)
    assert exc.value.code == "NOT_FOUND"


def test_tc_6_1_05_off_topic_turn_changes_nothing(service):
    """US-6 / AC-6.1 / TC-6.1.05 [edge] A declined request with no tool calls leaves the data untouched."""
    agent, llm = make_agent(service, say(f"I can only help with reservations. Please call us at {RESTAURANT_PHONE}."))
    reply = agent.handle(new_session(), "How much is the lasagna?")
    assert RESTAURANT_PHONE in reply and service.list_for_date(SATURDAY) == []
    assert all(m["role"] != "tool" for m in llm.requests[0])


# --- AC-6.2 Suggest calling the restaurant -----------------------------------------------------------


def test_tc_6_2_01_prompt_gives_the_restaurant_number_for_redirects():
    """US-6 / AC-6.2 / TC-6.2.01 [happy]"""
    prompt = build_system_prompt(NOW, RESTAURANT_PHONE)
    assert f"suggest calling the restaurant at {RESTAURANT_PHONE}" in prompt


def test_tc_6_2_02_party_over_eight_is_told_to_call(service):
    """US-6 / AC-6.2 / TC-6.2.02 [failure] BR-3: "Larger parties must call." """
    with pytest.raises(ReservationError) as exc:
        service.check_availability(SATURDAY, "19:00", 12)
    assert exc.value.code == "PARTY_TOO_LARGE" and "call the restaurant" in exc.value.message


def test_tc_6_2_03_outage_message_suggests_calling(tmp_path):
    """US-6 / AC-6.2 / TC-6.2.03 [failure] When the assistant is unavailable the guest gets the number."""
    client = TestClient(create_app(app_settings(tmp_path / "o.db"), complete=None, clock=lambda: NOW))
    response = client.post("/api/chat", json={"message": "Table for 2?"})
    assert response.status_code == 503 and RESTAURANT_PHONE in response.json()["error"]["message"]


def test_tc_6_2_04_stuck_conversation_suggests_calling(service):
    """US-6 / AC-6.2 / TC-6.2.04 [edge] Hitting the tool-round limit ends with the phone number."""
    loops = [call("check_availability", _id=f"c{i}", date=SATURDAY, time="19:00", party_size=2)
             for i in range(MAX_TOOL_ROUNDS)]
    agent, _ = make_agent(service, *loops)
    assert RESTAURANT_PHONE in agent.handle(new_session(), "hmm")


def test_tc_6_2_05_missing_code_is_redirected_to_the_phone():
    """US-6 / AC-6.2 / TC-6.2.05 [prompt-level] Changes without a code -> call the restaurant (D-4)."""
    assert "If the guest doesn't have it, explain that and suggest calling the restaurant" in \
        build_system_prompt(NOW, RESTAURANT_PHONE)
