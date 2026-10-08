"""A tool-use loop that ends with a validated, structured answer.

The model works with read-only tools, then must call the submit tool. Its input is validated against a
Pydantic model plus an optional check (for example: only cite sources you actually looked at). An
invalid submission goes back as a tool error so the model can fix it, a few times at most.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from revenueops.agents.llm import LLMClient, Usage

MAX_TOOL_OUTPUT_CHARS = 20_000


class AgentFailed(Exception):
    """The agent did not produce a valid answer within its budget."""


class ToolFailed(Exception):
    """Raised by a tool to report an error the model should see (not a crash)."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], str]

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    is_error: bool
    duration_ms: int


@dataclass
class AgentResult[T: BaseModel]:
    output: T
    tool_calls: list[ToolCall]
    usage: Usage
    turns: int
    duration_ms: int
    model: str


@dataclass
class Submit[T: BaseModel]:
    """The tool that ends the run. `check` raises ValueError to reject a well-formed but wrong answer."""

    name: str
    description: str
    schema: type[T]
    check: Callable[[T, list[ToolCall]], None] = field(default=lambda output, calls: None)

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.schema.model_json_schema()}


def run_agent[T: BaseModel](
    llm: LLMClient,
    *,
    system: str,
    prompt: str,
    tools: list[Tool],
    submit: Submit[T],
    max_turns: int = 8,
    max_bad_submissions: int = 2,
) -> AgentResult[T]:
    started = time.monotonic()
    by_name = {t.name: t for t in tools}
    definitions = [t.definition() for t in tools] + [submit.definition()]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    calls: list[ToolCall] = []
    usage = Usage()
    bad_submissions = 0

    for turn in range(1, max_turns + 1):
        response = llm.create(system=system, messages=messages, tools=definitions)
        usage += response.usage
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            raise AgentFailed("The model declined the request")

        tool_uses = response.tool_uses
        if not tool_uses:
            # It answered in prose instead of submitting. Ask once more, within the turn budget.
            messages.append({"role": "user", "content": f"Call the {submit.name} tool with your findings now."})
            continue

        results: list[dict[str, Any]] = []
        submitted: T | None = None
        for use in tool_uses:
            t0 = time.monotonic()
            name, args = use["name"], use.get("input") or {}
            if name == submit.name:
                try:
                    candidate = submit.schema.model_validate(args)
                    submit.check(candidate, calls)
                except (ValidationError, ValueError) as e:
                    bad_submissions += 1
                    content, is_error = f"Your submission was rejected: {e}", True
                else:
                    submitted, content, is_error = candidate, "Accepted.", False
            elif name in by_name:
                try:
                    content, is_error = _truncate(by_name[name].run(args)), False
                except ToolFailed as e:
                    content, is_error = str(e), True
            else:
                content, is_error = f"Unknown tool: {name}", True

            calls.append(ToolCall(name, args, is_error, round((time.monotonic() - t0) * 1000)))
            results.append({"type": "tool_result", "tool_use_id": use["id"], "content": content, "is_error": is_error})

        if submitted is not None:
            return AgentResult(
                output=submitted,
                tool_calls=calls,
                usage=usage,
                turns=turn,
                duration_ms=round((time.monotonic() - started) * 1000),
                model=llm.model,
            )
        if bad_submissions > max_bad_submissions:
            raise AgentFailed(f"Submission rejected {bad_submissions} times; last error: {results[-1]['content']}")
        messages.append({"role": "user", "content": results})

    raise AgentFailed(f"No valid submission after {max_turns} turns")


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:MAX_TOOL_OUTPUT_CHARS] + f"\n[truncated: {len(text) - MAX_TOOL_OUTPUT_CHARS} more characters]"


def to_json(model: BaseModel | list[Any] | dict[str, Any]) -> str:
    """Compact JSON for a tool result."""
    if isinstance(model, BaseModel):
        return model.model_dump_json(exclude_none=True)
    return json.dumps(model, default=str, ensure_ascii=False)
