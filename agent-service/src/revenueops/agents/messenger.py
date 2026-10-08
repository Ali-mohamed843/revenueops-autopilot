"""The Messenger: drafts the text of a customer message. Nothing else.

Who receives it, on which channel, and when, are decided by code (COMM-2). The draft is checked before
it is accepted: it must include the facts it was given (an order reference, a discount code) and must
not mention risk scores or past refusals (COMM-5). Messages land in the outbox; v1 never sends them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from revenueops.agents.llm import LLMClient
from revenueops.agents.loop import AgentResult, Submit, ToolCall, run_agent

MAX_CHARS = 700  # one WhatsApp screen; SMS splits but stays readable

# COMM-5: never mention the customer's risk score or refusal history.
FORBIDDEN = re.compile(
    r"risk|score|refus|blacklist|block|fraud|مخاطر|تقييم|رفض|رفضت|احتيال|حظر|قائمة سوداء",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MessageBrief:
    purpose: str  # what the message must achieve, in plain words
    facts: dict[str, str]  # what it may say: names, amounts, dates, codes
    must_include: tuple[str, ...] = ()  # exact strings the text must contain
    language: Literal["ar", "en"] = "ar"  # COMM-3: Egyptian Arabic unless the customer wrote in English
    channel: str = "whatsapp"
    store_name: str = "StoreForge"
    extra_rules: tuple[str, ...] = field(default=())


class Draft(BaseModel):
    language: Literal["ar", "en"]
    body: str = Field(min_length=10, max_length=MAX_CHARS)


SYSTEM_PROMPT = f"""\
You write short customer messages for an Egyptian online marketplace. Write like a friendly, \
respectful customer-service agent.

Rules:
- Write in the language you are asked for. Arabic means natural Egyptian Arabic, not formal Arabic.
- Use only the facts you are given. Never invent prices, dates, links, phone numbers or promises.
- Include every item listed under "must include" exactly as written.
- Never mention risk, scores, past refusals, returns history, or anything that sounds like suspicion.
- No pressure or threats. One clear request or piece of news, then a polite close.
- At most {MAX_CHARS} characters. No markdown.
- Finish by calling submit_message exactly once.
"""


def build_prompt(brief: MessageBrief) -> str:
    lines = [
        f"Purpose: {brief.purpose}",
        f"Language: {'Egyptian Arabic' if brief.language == 'ar' else 'English'} ({brief.language})",
        f"Channel: {brief.channel}",
        f"Sign as: {brief.store_name}",
        "Facts you may use:",
        *(f"  - {k}: {v}" for k, v in brief.facts.items()),
    ]
    if brief.must_include:
        lines.append("Must include exactly: " + ", ".join(brief.must_include))
    lines.extend(f"Also: {r}" for r in brief.extra_rules)
    return "\n".join(lines)


def make_check(brief: MessageBrief) -> Callable[[Draft, list[ToolCall]], None]:
    def check(draft: Draft, calls: list[ToolCall]) -> None:
        if draft.language != brief.language:
            raise ValueError(f"write it in {brief.language}")
        missing = [s for s in brief.must_include if s not in draft.body]
        if missing:
            raise ValueError(f"the message must include exactly: {', '.join(missing)}")
        bad = FORBIDDEN.search(draft.body)
        if bad:
            raise ValueError(f"remove {bad.group(0)!r}: messages never mention risk, scores or refusals (COMM-5)")

    return check


def draft_message(brief: MessageBrief, llm: LLMClient) -> AgentResult[Draft]:
    return run_agent(
        llm,
        system=SYSTEM_PROMPT,
        prompt=build_prompt(brief),
        tools=[],
        submit=Submit(
            name="submit_message",
            description="Submit the message text. Call exactly once.",
            schema=Draft,
            check=make_check(brief),
        ),
        max_turns=4,
    )
