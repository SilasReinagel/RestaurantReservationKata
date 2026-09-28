"""Shared helpers for the BRD acceptance tests (tests/brd). See tests/brd/TRACEABILITY.md."""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app import db
from app.agent import Agent, SessionStore
from app.config import Settings
from tests.test_agent import FakeLLM, call, say  # noqa: F401  (re-exported for the US modules)

RESTAURANT_PHONE = "(555) 010-0100"
SATURDAY = "2026-10-03"   # NOW is Tuesday 2026-09-29 12:00 America/New_York
SUNDAY = "2026-10-04"
MONDAY = "2026-10-05"
DEMO_DAY = "2026-09-30"   # "tomorrow": where the seed puts BV-DEMO and the §10.2 filler bookings


def new_session():
    return SessionStore().get_or_create(None)[0]


def make_agent(service, *responses):
    llm = FakeLLM(*responses)
    return Agent(service, llm, RESTAURANT_PHONE), llm


def result_of(llm: FakeLLM, call_id: str) -> dict:
    """The tool result the model received for a given tool call id."""
    for request in reversed(llm.requests):
        for message in request:
            if message["role"] == "tool" and message["tool_call_id"] == call_id:
                return json.loads(message["content"])
    raise AssertionError(f"no tool result for {call_id}")


def preview_then_confirm(service, tool: str, args: dict, session=None):
    """Drive the two-step flow: turn 1 previews, turn 2 (guest says yes) saves.
    Returns (preview_result, confirm_result, llm)."""
    session = session or new_session()
    agent, llm = make_agent(
        service,
        call(tool, _id="preview", **args), say("Here are the details. Shall I go ahead?"),
        call(tool, _id="confirm", **args), say("All done."),
    )
    agent.handle(session, "request")
    agent.handle(session, "Yes")
    return result_of(llm, "preview"), result_of(llm, "confirm"), llm


def tables_of(db_path: Path, code: str) -> list[str]:
    conn = db.connect(db_path)
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT table_id FROM reservation_tables rt JOIN reservations r ON r.id = rt.reservation_id "
            "WHERE r.confirmation_code = ?", (code,)))
    finally:
        conn.close()


def booking(date=SATURDAY, time="19:00", party_size=4, guest_name="Alex Rivera", phone="555-0199", notes=None):
    return dict(date=date, time=time, party_size=party_size, guest_name=guest_name, phone=phone, notes=notes)


def modification(code, date=None, time=None, party_size=None, notes=None):
    return dict(confirmation_code=code, date=date, time=time, party_size=party_size, notes=notes)


def app_settings(db_path: Path) -> Settings:
    return Settings(
        db_path=db_path, timezone=ZoneInfo("America/New_York"), restaurant_phone=RESTAURANT_PHONE,
        openai_model="test-model", openai_api_key=None, reasoning_effort=None, seed_demo_scenarios=True,
    )
