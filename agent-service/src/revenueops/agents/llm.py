"""One small interface over the model, so Claude and OpenRouter are a configuration choice.

Same rules as claude-agent-cli: Claude models get adaptive thinking and an effort level; any other
model behind an Anthropic-compatible endpoint (OpenRouter) gets only the core Messages API fields,
because the Claude-specific ones may be rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from revenueops.config import Settings

CLAUDE_MAX_TOKENS = 16_000
OTHER_MODEL_MAX_TOKENS = 8_000  # most non-Claude models cap output lower than Claude does


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
        response = self.client.messages.create(**self.request_params(system, messages, tools))
        return LLMResponse(
            # exclude_none keeps the blocks valid to send back on the next turn
            content=[block.model_dump(exclude_none=True) for block in response.content],
            stop_reason=response.stop_reason,
            usage=Usage(response.usage.input_tokens, response.usage.output_tokens),
        )
