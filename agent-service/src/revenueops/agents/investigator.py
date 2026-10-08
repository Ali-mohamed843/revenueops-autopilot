"""The Investigator: gathers the facts behind a case and explains what is going on.

It does not decide what to do. Phase 3's strategist proposes actions and a deterministic engine
scores them; this agent only has read-only tools.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from revenueops.adapters.base import NotFound, StoreAdapter, StoreError
from revenueops.agents.llm import LLMClient
from revenueops.agents.loop import AgentResult, Submit, Tool, ToolCall, ToolFailed, run_agent, to_json
from revenueops.cases.models import Case, SubjectType

Source = Literal[
    "case_signals",  # what the detector recorded (always available)
    "order",
    "status_history",
    "shipment",
    "delivery_attempts",
    "confirmations",
    "returns",
    "customer_profile",
]

# Which tool a source comes from. A report may only cite sources it actually looked at.
SOURCE_TOOL: dict[str, str | None] = {
    "case_signals": None,
    "order": "get_order",
    "status_history": "get_order",
    "shipment": "get_order",
    "delivery_attempts": "get_order",
    "confirmations": "get_order",
    "returns": "get_order",
    "customer_profile": "get_customer_profile",
}


class Evidence(BaseModel):
    fact: str = Field(max_length=300, description="One concrete fact, with numbers and dates where relevant")
    source: Source


class InvestigationReport(BaseModel):
    summary: str = Field(max_length=800, description="What is going on, in 2-4 sentences")
    likely_cause: str = Field(max_length=300, description="The most likely reason this revenue is at risk")
    evidence: list[Evidence] = Field(min_length=1, max_length=12)
    risk_factors: list[str] = Field(default_factory=list, max_length=8)
    mitigating_factors: list[str] = Field(default_factory=list, max_length=8)
    missing_information: list[str] = Field(
        default_factory=list, max_length=6, description="Facts you needed but the tools could not provide"
    )
    confidence: float = Field(ge=0, le=1, description="How sure you are of likely_cause")


SYSTEM_PROMPT = """\
You investigate revenue-at-risk cases for an Egyptian e-commerce marketplace where most orders are \
cash on delivery (COD). A COD order is only revenue once the courier collects the cash; refused or \
undeliverable orders cost the shipping fee and earn nothing.

Your job is to establish the facts behind one case and explain what is going on. You do NOT decide \
or recommend actions; another step does that.

Rules:
- Use the tools to look at the order and the customer before concluding. Do not guess.
- Every evidence item must state a fact you saw, and name the source it came from.
- Money is in EGP. Quote amounts, dates, counts and risk scores exactly as the tools return them.
- Risk scores are 0-100; higher means more likely to be refused or fail delivery.
- If something you need is not available, list it under missing_information instead of assuming.
- Finish by calling submit_investigation exactly once.
"""


def build_prompt(case: Case) -> str:
    lines = [
        f"Case type: {case.case_type}",
        f"Title: {case.title}",
        f"Subject: {case.subject_type} {case.subject_id}",
        f"Value at risk: {case.value_at_risk} {case.currency}",
        f"Detector signals (source 'case_signals'): {json.dumps(case.signals, ensure_ascii=False, default=str)}",
    ]
    if case.subject_type == SubjectType.ORDER:
        lines.append(f"Start with get_order('{case.subject_id}'), then look at the customer.")
    elif case.subject_type == SubjectType.RETURN:
        lines.append(f"The return belongs to order {case.signals.get('order_id')}: look at that order.")
    elif case.subject_type == SubjectType.CART:
        lines.append("The cart's contents are in the signals. Look at the customer's history if you can.")
    return "\n".join(lines)


def build_tools(store: StoreAdapter) -> list[Tool]:
    def get_order(args: dict[str, Any]) -> str:
        order_id = str(args.get("order_id", ""))
        try:
            return to_json(store.get_order(order_id))
        except NotFound as e:
            raise ToolFailed(f"No order {order_id}") from e
        except StoreError as e:
            raise ToolFailed(f"The store could not answer: {e}") from e

    def get_customer_profile(args: dict[str, Any]) -> str:
        key = str(args.get("customer_key", ""))
        try:
            profile = store.get_customer(key)
        except StoreError as e:
            raise ToolFailed(f"The store could not answer: {e}") from e
        return to_json(profile) if profile else "The store has no history for this customer."

    return [
        Tool(
            name="get_order",
            description=(
                "Full order: status, lines, totals, status history, notes, shipment with delivery attempts, "
                "confirmation messages, returns and the customer's key."
            ),
            input_schema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
            run=get_order,
        ),
        Tool(
            name="get_customer_profile",
            description=(
                "The customer's track record: COD risk score, deliveries, refusals, returns, unanswered "
                "delivery calls, order counts by status. Use the customer key from the order or case signals."
            ),
            input_schema={
                "type": "object",
                "properties": {"customer_key": {"type": "string"}},
                "required": ["customer_key"],
            },
            run=get_customer_profile,
        ),
    ]


def check_grounding(report: InvestigationReport, calls: list[ToolCall]) -> None:
    """Reject evidence attributed to a source the agent never successfully looked at."""
    looked_at = {c.name for c in calls if not c.is_error}
    unseen = sorted(
        {e.source for e in report.evidence if (tool := SOURCE_TOOL[e.source]) is not None and tool not in looked_at}
    )
    if unseen:
        raise ValueError(
            f"evidence cites {', '.join(unseen)} but you never retrieved it. "
            "Call the tool first, or cite only sources you looked at."
        )


def investigate(case: Case, store: StoreAdapter, llm: LLMClient) -> AgentResult[InvestigationReport]:
    return run_agent(
        llm,
        system=SYSTEM_PROMPT,
        prompt=build_prompt(case),
        tools=build_tools(store),
        submit=Submit(
            name="submit_investigation",
            description="Submit your findings. Call exactly once, when you are done.",
            schema=InvestigationReport,
            check=check_grounding,
        ),
    )
