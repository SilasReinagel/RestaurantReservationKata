from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import NOW
from tests.test_agent import FakeLLM, call, say


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "api.db",
        timezone=ZoneInfo("America/New_York"),
        restaurant_phone="(555) 010-0100",
        openai_model="test-model",
        openai_api_key=None,
        reasoning_effort=None,
        seed_demo_scenarios=True,
    )


@pytest.fixture
def make_client(tmp_path):
    def factory(*responses):
        llm = FakeLLM(*responses) if responses else None
        return TestClient(create_app(settings_for(tmp_path), complete=llm, clock=lambda: NOW)), llm
    return factory


def test_index_and_static_are_served(make_client):
    client, _ = make_client()
    assert "Bella Vista" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_chat_round_trip_keeps_session(make_client):
    client, llm = make_client(say("How many guests?"), say("Great."))
    first = client.post("/api/chat", json={"message": "Book Saturday 7pm"}).json()
    assert first["reply"] == "How many guests?" and first["session_reset"] is False
    second = client.post("/api/chat", json={"session_id": first["session_id"], "message": "4"}).json()
    assert second["session_id"] == first["session_id"]
    assert [m["role"] for m in llm.requests[1]] == ["system", "user", "assistant", "user"]


def test_chat_uses_seeded_demo_reservation(make_client):
    client, llm = make_client(call("get_reservation", confirmation_code="bv-demo"), say("Found it."))
    client.post("/api/chat", json={"message": "Look up bv-demo"})
    tool_msg = next(m for m in llm.requests[1] if m["role"] == "tool")
    assert '"guest_name": "Alex Rivera"' in tool_msg["content"]


def test_chat_validation_errors(make_client):
    client, _ = make_client()
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400
    assert client.post("/api/chat", json={"message": "x" * 1001}).json()["error"]["code"] == "MESSAGE_TOO_LONG"


def test_chat_without_api_key_returns_503(make_client):
    client, _ = make_client()
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 503 and "(555) 010-0100" in response.json()["error"]["message"]


def test_stale_session_is_flagged(make_client):
    client, _ = make_client(say("Hello again"))
    body = client.post("/api/chat", json={"session_id": "gone-after-restart", "message": "hi"}).json()
    assert body["session_reset"] is True and body["session_id"] != "gone-after-restart"


def test_reservations_endpoint_shows_seed_data(make_client):
    client, _ = make_client()
    rows = client.get("/api/reservations", params={"date": "2026-09-30"}).json()
    assert {r["confirmation_code"] for r in rows} == {"BV-DEMO", "BV-SEDA", "BV-SEDB"}
    assert client.get("/api/reservations", params={"date": "nope"}).status_code == 400
