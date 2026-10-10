"""One small interface over the model, so the provider is a configuration choice.

The agents speak the Anthropic Messages format internally. Two clients:
- AnthropicLLM: Claude directly, or an Anthropic-compatible endpoint (OpenRouter). Same rules as
  claude-agent-cli: Claude models get adaptive thinking and an effort level; other models get only
  the core fields, because the Claude-specific ones may be rejected.
- OpenAICompatibleLLM: any OpenAI chat-completions endpoint (CodeCraft and similar). It translates
  messages, tools and responses to and from the Anthropic format.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx

from revenueops.config import Settings

CLAUDE_MAX_TOKENS = 16_000
MAX_RETRIES = 4  # the SDK backs off between tries; free models are often briefly rate-limited
OTHER_MODEL_MAX_TOKENS = 8_000  # most non-Claude models cap output lower than Claude does
REQUEST_TIMEOUT = 180  # seconds to wait for one answer; reasoning models can think for a while
CALL_BUDGET = 360  # seconds for one call, retries included, so a provider that stops answering can't hang a run


class LLMError(Exception):
    """The model API failed (after the SDK's own retries)."""


class RateLimited(LLMError):
    """The model is rate-limited; more calls right now will fail too."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __iadd__(self, other: Usage) -> Usage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        return self


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]  # Messages API content blocks, as plain dicts
    stop_reason: str | None
    usage: Usage = field(default_factory=Usage)

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")


class LLMClient(Protocol):
    model: str

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...


class AnthropicLLM:
    """Claude directly, or any model behind an Anthropic-compatible endpoint such as OpenRouter."""

    def __init__(self, client: anthropic.Anthropic, model: str, effort: str = "medium") -> None:
        self.client = client
        self.model = model
        self.effort = effort

    @classmethod
    def from_settings(cls, settings: Settings) -> AnthropicLLM:
        if not (settings.anthropic_api_key or settings.anthropic_auth_token):
            raise ValueError(
                "No model credentials. Set ANTHROPIC_API_KEY (Claude) or "
                "ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN (OpenRouter) in .env"
            )
        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None,
            auth_token=settings.anthropic_auth_token or None,
            base_url=settings.anthropic_base_url or None,
            max_retries=MAX_RETRIES,
        )
        return cls(client, settings.agent_model)

    @property
    def is_claude(self) -> bool:
        return self.model.startswith("claude-")

    def request_params(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"model": self.model, "system": system, "messages": messages, "tools": tools}
        if not self.is_claude:
            params["max_tokens"] = OTHER_MODEL_MAX_TOKENS
            return params
        params["max_tokens"] = CLAUDE_MAX_TOKENS
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": self.effort}
        return params

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        try:
            response = self.client.messages.create(**self.request_params(system, messages, tools))
        except anthropic.RateLimitError as e:
            raise RateLimited(f"{self.model} is rate-limited: {_reason(e)}") from e
        except anthropic.APIError as e:
            raise LLMError(f"{self.model} failed: {_reason(e)}") from e
        return LLMResponse(
            # exclude_none keeps the blocks valid to send back on the next turn
            content=[block.model_dump(exclude_none=True) for block in response.content],
            stop_reason=response.stop_reason,
            usage=Usage(response.usage.input_tokens, response.usage.output_tokens),
        )


def _reason(e: anthropic.APIError) -> str:
    """The provider's own explanation when there is one."""
    body: dict[str, Any] = e.body if isinstance(e.body, dict) else {}
    error: dict[str, Any] = body["error"] if isinstance(body.get("error"), dict) else body
    # OpenRouter puts the useful explanation in metadata.raw, next to "error"
    metadata = next((m for m in (body.get("metadata"), error.get("metadata")) if isinstance(m, dict)), {})
    message = metadata.get("raw") or error.get("message") or e.message
    status = getattr(e, "status_code", None)
    return f"HTTP {status}: {message}" if status else str(message)


class OpenAICompatibleLLM:
    """Any OpenAI chat-completions endpoint, behind the same interface as AnthropicLLM."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self._sleep = sleep
        self._clock = clock
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAICompatibleLLM:
        if not (settings.openai_base_url and settings.openai_api_key):
            raise ValueError("LLM_PROVIDER=openai needs OPENAI_BASE_URL and OPENAI_API_KEY in .env")
        return cls(settings.openai_base_url, settings.openai_api_key, settings.agent_model)

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        body = {
            "model": self.model,
            "max_tokens": OTHER_MODEL_MAX_TOKENS,
            "messages": to_openai_messages(system, messages),
            "tools": [
                {
                    "type": "function",
                    "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
                }
                for t in tools
            ],
        }
        data = self._post(body)
        choice = data["choices"][0]
        finish = choice.get("finish_reason")
        usage = data.get("usage") or {}
        return LLMResponse(
            content=from_openai_message(choice["message"]),
            stop_reason=_STOP_REASONS.get(finish, finish) if finish else None,
            usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST with backoff on rate limits, server errors and network errors, like the Anthropic SDK.

        Retries stop at MAX_RETRIES or when the next try wouldn't fit in CALL_BUDGET, whichever is first.
        """
        deadline = self._clock() + CALL_BUDGET
        error: LLMError
        for attempt in range(MAX_RETRIES + 1):
            timeout = min(REQUEST_TIMEOUT, deadline - self._clock())
            try:
                response = self._http.post("/chat/completions", json=body, timeout=timeout)
            except httpx.HTTPError as e:
                error = LLMError(f"{self.model} is unreachable: {type(e).__name__} {e}".strip())
            else:
                status = response.status_code
                if status < 400:
                    result: dict[str, Any] = response.json()
                    return result
                reason = f"HTTP {status}: {_openai_error(response)}"
                if status == 429:
                    error = RateLimited(f"{self.model} is rate-limited: {reason}")
                else:
                    error = LLMError(f"{self.model} failed: {reason}")
                    if status < 500:
                        raise error
            pause = min(2**attempt, 30)
            if attempt == MAX_RETRIES or self._clock() + pause >= deadline:
                raise error
            self._sleep(pause)
        raise AssertionError("unreachable")


_STOP_REASONS = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-format conversation -> OpenAI chat messages."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)},
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            message: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = calls
            out.append(message)
        else:  # a user turn of tool results (and possibly text)
            for b in content:
                if b.get("type") == "tool_result":
                    result = f"ERROR: {b['content']}" if b.get("is_error") else b["content"]
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": result})
                elif b.get("type") == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


def from_openai_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    """OpenAI assistant message -> Anthropic content blocks. Reasoning text is dropped."""
    blocks: list[dict[str, Any]] = []
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}  # the submit tool's validation reports the problem back to the model
        blocks.append(
            {
                "type": "tool_use",
                "id": call["id"],
                "name": call["function"]["name"],
                "input": args if isinstance(args, dict) else {},
            }
        )
    return blocks


def _openai_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return str(body)[:300]


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "openai":
        return OpenAICompatibleLLM.from_settings(settings)
    if settings.llm_provider == "anthropic":
        return AnthropicLLM.from_settings(settings)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r} (use anthropic or openai)")
