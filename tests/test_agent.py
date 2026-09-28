import json

import pytest

from app.agent import MAX_TOOL_ROUNDS, TOOLS, Agent, LLMUnavailable, SessionStore, build_system_prompt
from tests.conftest import NOW


class FakeLLM:
    """Replays scripted responses and records every request it receives."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, messages, tools):
        self.requests.append(json.loads(json.dumps(messages)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def say(text):
    return {"content": text, "tool_calls": [], "finish_reason": "stop"}


def call(name, _id="call_1", **arguments):
    return {"content": None, "tool_calls": [{"id": _id, "name": name, "arguments": json.dumps(arguments)}],
            "finish_reason": "tool_calls"}


BOOKING = dict(date="2026-10-03", time="19:00", party_size=4, guest_name="Alex Rivera", phone="555-0100",
               notes="anniversary")


@pytest.fixture
def session():
    return SessionStore().get_or_create(None)[0]


def make_agent(service, *responses):
    llm = FakeLLM(*responses)
    return Agent(service, llm, "(555) 010-0100"), llm


def tool_results(llm_request):
    return [json.loads(m["content"]) for m in llm_request if m["role"] == "tool"]


def test_tool_schemas_are_openai_strict():
    for tool in TOOLS:
        params = tool["function"]["parameters"]
        assert tool["function"]["strict"] is True
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])


def test_system_prompt_has_calendar_and_phone():
    prompt = build_system_prompt(NOW, "(555) 010-0100")
    assert "2026-09-29 Tuesday open today" in prompt
    assert "2026-10-05 Monday CLOSED" in prompt
    assert "(555) 010-0100" in prompt


def test_booking_happy_path_previews_then_saves_on_next_turn(service, session):
    agent, llm = make_agent(
        service,
        call("create_reservation", **BOOKING), say("Shall I book it?"),
        call("create_reservation", _id="call_2", **BOOKING), say("Booked!"),
    )
    agent.handle(session, "4 people Saturday 7pm, Alex Rivera, 555-0100, it's our anniversary")
    preview = tool_results(llm.requests[1])[0]
    assert preview["error_code"] == "CONFIRMATION_REQUIRED"
    assert preview["preview"]["when"] == "Saturday, October 3 at 7:00 PM"
    assert "confirmation_code" not in preview["preview"]
    assert service.list_for_date("2026-10-03") == []  # nothing saved by the preview

    agent.handle(session, "Yes")
    result = tool_results(llm.requests[3])[-1]
    assert result["ok"] and service.get(result["reservation"]["confirmation_code"])["notes"] == "anniversary"
    assert session.pending is None


def test_create_repeated_in_the_same_turn_is_not_saved(service, session):
    agent, _ = make_agent(
        service,
        call("create_reservation", **BOOKING),
        call("create_reservation", _id="call_2", **BOOKING),
        say("Shall I book it?"),
    )
    agent.handle(session, "Book it")
    assert service.list_for_date("2026-10-03") == []
    assert session.pending.turn == 1  # the repeat didn't reset or satisfy the proposal


def test_changed_details_need_a_fresh_confirmation(service, session):
    later = {**BOOKING, "time": "20:00"}
    agent, _ = make_agent(
        service,
        call("create_reservation", **BOOKING), say("7pm OK?"),
        call("create_reservation", _id="call_2", **later), say("8pm OK?"),  # guest said "make it 8"
        call("create_reservation", _id="call_3", **later), say("Booked for 8."),
    )
    agent.handle(session, "Book 7pm")
    agent.handle(session, "Actually make it 8pm")
    assert service.list_for_date("2026-10-03") == []
    agent.handle(session, "Yes")
    assert [r["time"] for r in service.list_for_date("2026-10-03")] == ["20:00"]


def test_confirmation_tolerates_reworded_notes_and_name_case(service, session):
    agent, _ = make_agent(
        service,
        call("create_reservation", **BOOKING), say("OK?"),
        call("create_reservation", _id="call_2", **{**BOOKING, "guest_name": "alex rivera",
                                                    "notes": "Anniversary dinner"}),
        say("Booked!"),
    )
    agent.handle(session, "Book it")
    agent.handle(session, "Yes")
    [saved] = service.list_for_date("2026-10-03")
    assert saved["notes"] == "Anniversary dinner"  # the latest wording is stored


def test_invalid_proposal_returns_the_rule_error_and_records_nothing(service, session):
    agent, llm = make_agent(service, call("create_reservation", **{**BOOKING, "date": "2026-10-05"}),
                            say("We're closed Mondays."))
    agent.handle(session, "Monday please")
    assert tool_results(llm.requests[1])[0]["error_code"] == "CLOSED_DAY"
    assert session.pending is None


def test_modify_requires_confirmation(service, session):
    code = service.create("2026-10-03", "19:00", 4, "Alex Rivera", "555-0100")["confirmation_code"]
    change = dict(confirmation_code=code, date=None, time="20:00", party_size=None, notes=None)
    agent, llm = make_agent(
        service,
        call("modify_reservation", **change), say("Move to 8pm?"),
        call("modify_reservation", _id="call_2", **change), say("Done."),
    )
    agent.handle(session, f"Move {code} to 8pm")
    preview = tool_results(llm.requests[1])[0]
    assert preview["error_code"] == "CONFIRMATION_REQUIRED" and preview["preview"]["time"] == "20:00"
    assert service.get(code)["time"] == "19:00"
    agent.handle(session, "Yes")
    assert service.get(code)["time"] == "20:00"


def test_preview_is_forgotten_if_the_guest_never_saw_it(service, session):
    agent, _ = make_agent(
        service,
        call("create_reservation", **BOOKING), RuntimeError("timeout"),  # reply with the preview never arrives
        call("create_reservation", _id="call_2", **BOOKING), say("Shall I book it?"),
    )
    with pytest.raises(LLMUnavailable):
        agent.handle(session, "Book it")
    assert session.pending is None
    agent.handle(session, "Book it")  # retry: must preview again, not save
    assert service.list_for_date("2026-10-03") == []


def test_business_rule_errors_come_back_as_tool_results(service, session):
    agent, llm = make_agent(service, call("check_availability", date="2026-10-05", time="19:00", party_size=2),
                            say("We're closed Mondays."))
    agent.handle(session, "Monday at 7?")
    assert tool_results(llm.requests[1])[0]["error_code"] == "CLOSED_DAY"


def test_invalid_json_arguments_are_reported_not_raised(service, session):
    bad = {"content": None, "tool_calls": [{"id": "c1", "name": "get_reservation", "arguments": "{oops"}],
           "finish_reason": "tool_calls"}
    agent, llm = make_agent(service, bad, say("Could you repeat the code?"))
    agent.handle(session, "BV-DEMO")
    assert tool_results(llm.requests[1])[0]["error_code"] == "INVALID_ARGUMENTS"


def test_cancel_requires_lookup_in_an_earlier_turn(service, session):
    code = service.create("2026-10-03", "19:00", 4, "Alex Rivera", "555-0100")["confirmation_code"]
    agent, llm = make_agent(
        service,
        # Turn 1: the model tries to cancel straight away -> blocked, then looks it up.
        call("cancel_reservation", confirmation_code=code),
        call("get_reservation", _id="call_2", confirmation_code=code),
        call("cancel_reservation", _id="call_3", confirmation_code=code),  # same turn as lookup -> still blocked
        say("That's Alex Rivera, party of 4. Cancel it?"),
        # Turn 2: guest said yes.
        call("cancel_reservation", _id="call_4", confirmation_code=code.lower()),
        say("Cancelled."),
    )
    agent.handle(session, f"Cancel {code}")
    assert service.get(code)["status"] == "confirmed"
    results = tool_results(llm.requests[3])
    assert [r.get("error_code") for r in results] == ["CONFIRMATION_REQUIRED", None, "CONFIRMATION_REQUIRED"]
    agent.handle(session, "Yes")
    assert service.get(code)["status"] == "cancelled"


def test_history_is_append_only_and_well_formed(service, session):
    agent, llm = make_agent(
        service,
        call("check_availability", date="2026-10-03", time="19:00", party_size=2),
        say("Available. Name?"),
        say("Thanks!"),
    )
    agent.handle(session, "Table for 2 Saturday 7pm")
    first_turn = list(session.messages)
    agent.handle(session, "Alex")
    assert session.messages[: len(first_turn)] == first_turn
    assert [m["role"] for m in session.messages] == ["user", "assistant", "tool", "assistant", "user", "assistant"]
    assert llm.requests[-1][0]["role"] == "system"  # system prompt is rebuilt, never stored in history


def test_llm_failure_before_any_change_discards_the_turn(service, session):
    agent, _ = make_agent(service, call("check_availability", date="2026-10-03", time="19:00", party_size=2),
                          RuntimeError("timeout"))
    with pytest.raises(LLMUnavailable):
        agent.handle(session, "Table for 2 Saturday 7pm")
    assert session.messages == []  # no dangling tool_calls that would break the next request


def test_llm_failure_after_a_booking_still_reports_the_booking(service, session):
    agent, _ = make_agent(
        service,
        call("create_reservation", **BOOKING), say("Shall I book it?"),
        call("create_reservation", _id="call_2", **BOOKING), RuntimeError("timeout"),
    )
    agent.handle(session, "Book it")
    reply = agent.handle(session, "Yes")
    code = service.list_for_date("2026-10-03")[0]["confirmation_code"]
    assert code in reply and "Saturday, October 3 at 7:00 PM" in reply
    assert session.messages[-1] == {"role": "assistant", "content": reply}


def test_tool_round_limit(service, session):
    loops = [call("check_availability", _id=f"c{i}", date="2026-10-03", time="19:00", party_size=2)
             for i in range(MAX_TOOL_ROUNDS)]
    agent, _ = make_agent(service, *loops)
    reply = agent.handle(session, "hmm")
    assert "(555) 010-0100" in reply


def test_unconfigured_llm_raises(service, session):
    with pytest.raises(LLMUnavailable):
        Agent(service, None, "x").handle(session, "hi")


def test_unknown_session_id_creates_fresh_session_and_flags_reset():
    store = SessionStore()
    first, reset = store.get_or_create(None)
    assert reset is False
    assert store.get_or_create(first.id) == (first, False)
    fresh, reset = store.get_or_create("stale-id-from-before-restart")
    assert reset is True and fresh.id != first.id
