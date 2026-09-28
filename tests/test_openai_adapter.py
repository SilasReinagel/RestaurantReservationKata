"""Checks the OpenAI adapter's request shape and response parsing against a mocked HTTP transport."""

import json

import httpx2

from app.agent import TOOLS, openai_complete


def fake_openai(captured: list, message: dict, finish_reason: str):
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish_reason}],
        })
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_adapter_sends_strict_tools_and_parses_tool_calls():
    captured = []
    tool_call = {"id": "call_1", "type": "function",
                 "function": {"name": "get_reservation", "arguments": '{"confirmation_code": "BV-DEMO"}'}}
    complete = openai_complete("sk-test", "test-model", http_client=fake_openai(
        captured, {"content": None, "tool_calls": [tool_call]}, "tool_calls"))

    result = complete([{"role": "user", "content": "hi"}], TOOLS)

    body = captured[0]
    assert body["model"] == "test-model" and body["parallel_tool_calls"] is False
    assert body["tools"][0]["function"]["strict"] is True
    assert result == {
        "content": None,
        "tool_calls": [{"id": "call_1", "name": "get_reservation", "arguments": '{"confirmation_code": "BV-DEMO"}'}],
        "finish_reason": "tool_calls",
    }


def test_adapter_parses_text_and_passes_reasoning_effort():
    captured = []
    complete = openai_complete("sk-test", "test-model", "low", http_client=fake_openai(
        captured, {"content": "Hello!"}, "stop"))
    assert complete([{"role": "user", "content": "hi"}], TOOLS)["content"] == "Hello!"
    assert captured[0]["reasoning_effort"] == "low"
