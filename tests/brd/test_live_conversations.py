"""Live conversation checks against the real OpenAI model (opt-in, costs API credits).

These cover the acceptance criteria that only model behavior can satisfy: collecting details in
conversation, phrasing, polite refusals. They're skipped unless BV_LIVE_TESTS=1 and OPENAI_API_KEY
are set:

    set BV_LIVE_TESTS=1 && python -m pytest tests/brd/test_live_conversations.py -v

The checks are deliberately loose (database state first, then a few phrases), because wording varies
from run to run.
"""

import os
import re

import pytest
from dotenv import load_dotenv

from app import db
from app.agent import Agent, SessionStore, openai_complete
from app.reservations import ReservationService, seed_demo_data
from tests.brd.helpers import DEMO_DAY, RESTAURANT_PHONE, SATURDAY
from tests.conftest import NOW, MutableClock

if os.getenv("BV_LIVE_TESTS") == "1":
    load_dotenv()

pytestmark = pytest.mark.skipif(
    os.getenv("BV_LIVE_TESTS") != "1" or not os.getenv("OPENAI_API_KEY"),
    reason="live LLM tests are opt-in: set BV_LIVE_TESTS=1 and OPENAI_API_KEY",
)

CODE = re.compile(r"\bBV-[A-Z0-9]{4}\b")
YES_AGAIN = "Yes, please go ahead."


@pytest.fixture
def live(tmp_path):
    path = tmp_path / "live.db"
    db.init_db(path)
    service = ReservationService(path, MutableClock(NOW))
    seed_demo_data(service, include_scenarios=True)
    complete = openai_complete(os.environ["OPENAI_API_KEY"], os.getenv("OPENAI_MODEL", "gpt-5-mini"),
                               os.getenv("OPENAI_REASONING_EFFORT") or None)
    agent = Agent(service, complete, RESTAURANT_PHONE)
    return service, agent, SessionStore().get_or_create(None)[0]


def converse(agent, session, *messages):
    return [agent.handle(session, m) for m in messages]


def invented_codes(service, replies):
    """Codes that appear in replies but don't exist in the data store."""
    known = {r["confirmation_code"] for d in (DEMO_DAY, SATURDAY) for r in service.list_for_date(d)}
    return {c for reply in replies for c in CODE.findall(reply)} - known


def test_l1_us1_happy_path_booking(live):
    """US-1 / AC-1.1-1.4 / L-1: details collected over several turns, confirmed, saved, and the code returned."""
    service, agent, session = live
    replies = converse(agent, session, "Hi, I'd like to book a table for Saturday at 7pm.", "4 of us.",
                       "Sam Lee, 555-0142.", "Yes, that's right.")
    if not service.list_for_date(SATURDAY):
        replies += converse(agent, session, YES_AGAIN)
    [saved] = [r for r in service.list_for_date(SATURDAY) if r["guest_name"] == "Sam Lee"]
    assert (saved["time"], saved["party_size"], saved["phone"]) == ("19:00", 4, "555-0142")
    assert saved["confirmation_code"] in replies[-1]
    assert not invented_codes(service, replies)


def test_l2_us2_alternatives_are_real(live):
    """US-2 / AC-2.1, AC-2.2 / L-2: BRD §10.2. The only real options are 9:00pm, 6:30pm and 9:30pm."""
    service, agent, session = live
    [reply] = converse(agent, session, "Table for 6 tomorrow at 8pm?")
    offered = set(re.findall(r"\b(\d{1,2}(?::\d{2})?)\s*(?:pm|PM|p\.m\.)", reply))
    assert offered, reply
    assert offered <= {"9", "9:00", "6:30", "9:30"}, reply
    assert len([r for r in service.list_for_date(DEMO_DAY) if r["status"] == "confirmed"]) == 3  # nothing booked


def test_l3_us4_cancel_bv_demo(live):
    """US-4 / AC-4.1-4.3 / L-3: BRD §10.3. Read back, confirm, cancel."""
    service, agent, session = live
    [first] = converse(agent, session, "I need to cancel BV-DEMO.")
    assert service.get("BV-DEMO")["status"] == "confirmed" and "Alex" in first
    converse(agent, session, "Yes.")
    assert service.get("BV-DEMO")["status"] == "cancelled"


def test_l4_us6_menu_price_is_declined(live):
    """US-6 / AC-6.1, AC-6.2 / L-4"""
    service, agent, session = live
    [reply] = converse(agent, session, "How much is the lasagna?")
    assert "$" not in reply and RESTAURANT_PHONE in reply


def test_l5_us6_food_order_is_declined(live):
    """US-6 / AC-6.1, AC-6.2 / L-5"""
    service, agent, session = live
    [reply] = converse(agent, session, "Can I order two margherita pizzas for pickup?")
    assert RESTAURANT_PHONE in reply
    assert len(service.list_for_date(SATURDAY)) == 0


def test_l6_us5_special_request_noted_not_promised(live):
    """US-5 / AC-5.1, AC-5.2 / L-6"""
    service, agent, session = live
    replies = converse(agent, session,
                       "Table for 2 on Saturday at 7pm for Sam Lee, 555-0142. We'd love a window seat.", "Yes.")
    if not service.list_for_date(SATURDAY):
        replies += converse(agent, session, YES_AGAIN)
    [saved] = service.list_for_date(SATURDAY)
    assert "window" in saved["notes"].lower()
    promise = re.compile(r"(you'll|you will) (get|have) (a|the) window|guarantee", re.IGNORECASE)
    assert not any(promise.search(r) for r in replies), replies


def test_l7_us3_modify_bv_demo(live):
    """US-3 / AC-3.1-3.3 / L-7"""
    service, agent, session = live
    converse(agent, session, "Can I move BV-DEMO to 8:30pm?", "Yes, please.")
    if service.get("BV-DEMO")["time"] != "20:30":
        converse(agent, session, YES_AGAIN)
    assert service.get("BV-DEMO")["time"] == "20:30"


def test_l8_us6_allergen_advice_is_not_given(live):
    """US-6 / AC-6.1 / L-8: allergens beyond noting them."""
    service, agent, session = live
    [reply] = converse(agent, session, "Is your tiramisu safe for someone with a severe nut allergy?")
    assert RESTAURANT_PHONE in reply
    assert not re.search(r"\b(it is|it's) (safe|nut-free)\b", reply, re.IGNORECASE), reply
