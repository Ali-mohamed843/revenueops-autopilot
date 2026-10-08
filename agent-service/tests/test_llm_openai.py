"""OpenAICompatibleLLM: translation to and from the OpenAI chat-completions format, retries, errors."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from revenueops.agents.llm import (
    LLMError,
    OpenAICompatibleLLM,
    RateLimited,
    build_llm,
    from_openai_message,
    to_openai_messages,
)
from revenueops.config import Settings

TOOLS = [{"name": "get_order", "description": "Get an order", "input_schema": {"type": "object", "properties": {}}}]


def completion(message: dict[str, Any], finish: str = "tool_calls") -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", **message}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def make(handler: Any, sleeps: list[float] | None = None) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        "https://llm.test/v1/",
        "cc_test",
        "glm-5.3",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


def test_conversation_translates_to_openai_messages() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Investigate case 1"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "x"},
                {"type": "text", "text": "Looking."},
                {"type": "tool_use", "id": "call_1", "name": "get_order", "input": {"order_id": "o1"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": '{"id": "o1"}', "is_error": False},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_2", "content": "No order x", "is_error": True}],
        },
    ]
    assert to_openai_messages("sys", messages) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Investigate case 1"},
        {
            "role": "assistant",
            "content": "Looking.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_order", "arguments": '{"order_id": "o1"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"id": "o1"}'},
        {"role": "tool", "tool_call_id": "call_2", "content": "ERROR: No order x"},
    ]


def test_response_translates_to_anthropic_blocks() -> None:
    blocks = from_openai_message(
        {
            "content": None,
            "reasoning_content": "dropped",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_order", "arguments": '{"order_id": "o1"}'}},
                {"id": "c2", "type": "function", "function": {"name": "submit", "arguments": "{not json"}},
            ],
        }
    )
    assert blocks == [
        {"type": "tool_use", "id": "c1", "name": "get_order", "input": {"order_id": "o1"}},
        {"type": "tool_use", "id": "c2", "name": "submit", "input": {}},
    ]


def test_create_sends_tools_and_maps_the_reply() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer cc_test"
        seen.append(json.loads(request.content))
        call = {"id": "c1", "type": "function", "function": {"name": "get_order", "arguments": '{"order_id":"o1"}'}}
        return httpx.Response(200, json=completion({"content": None, "tool_calls": [call]}))

    response = make(handler).create(system="sys", messages=[{"role": "user", "content": "go"}], tools=TOOLS)

    assert seen[0]["model"] == "glm-5.3"
    assert seen[0]["tools"] == [
        {
            "type": "function",
            "function": {"name": "get_order", "description": "Get an order", "parameters": TOOLS[0]["input_schema"]},
        }
    ]
    assert response.stop_reason == "tool_use"
    assert response.tool_uses == [{"type": "tool_use", "id": "c1", "name": "get_order", "input": {"order_id": "o1"}}]
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 30)


def test_retries_rate_limits_and_server_errors_with_backoff() -> None:
    replies = iter([httpx.Response(429, json={}), httpx.Response(502, text="bad gateway")])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replies, httpx.Response(200, json=completion({"content": "done"}, "stop")))

    sleeps: list[float] = []
    response = make(handler, sleeps).create(system="s", messages=[], tools=[])
    assert response.text == "done" and response.stop_reason == "end_turn"
    assert sleeps == [1, 2]


def test_persistent_rate_limit_raises_rate_limited() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Too many requests"}})

    sleeps: list[float] = []
    with pytest.raises(RateLimited, match="HTTP 429: Too many requests"):
        make(handler, sleeps).create(system="s", messages=[], tools=[])
    assert len(sleeps) == 4


def test_client_errors_fail_at_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    sleeps: list[float] = []
    with pytest.raises(LLMError, match="HTTP 401: Invalid API key"):
        make(handler, sleeps).create(system="s", messages=[], tools=[])
    assert sleeps == []


def test_provider_setting_picks_the_client(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER=openai\nOPENAI_BASE_URL=https://codecraftapi.com/v1\nOPENAI_API_KEY=cc_x\nAGENT_MODEL=glm-5.3\n"
    )
    llm = build_llm(Settings(_env_file=env))  # type: ignore[call-arg]
    assert isinstance(llm, OpenAICompatibleLLM) and llm.model == "glm-5.3"

    env.write_text("LLM_PROVIDER=openai\nAGENT_MODEL=glm-5.3\n")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_llm(Settings(_env_file=env))  # type: ignore[call-arg]
