"""The Strategist: proposes what to do about an investigated case.

It picks from the fixed action catalogue and cites the policy rules it relied on. It does not estimate
probabilities or decide who may act: decision/score.py does that, deterministically, afterwards.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from revenueops.agents.llm import LLMClient
from revenueops.agents.loop import AgentResult, Submit, ToolCall, run_agent
from revenueops.cases.models import Case
from revenueops.decision.catalogue import ActionSpec, Param
from revenueops.decision.escalation import step_for
from revenueops.policies import Policy


class ProposedAction(BaseModel):
    action: str = Field(description="An action key from the list you were given")
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(max_length=500, description="Why this action fits this case, in 1-3 sentences")
    policy_refs: list[str] = Field(default_factory=list, max_length=6, description="Rule ids, e.g. COD-2")


class Strategy(BaseModel):
    approach: str = Field(max_length=500, description="Your overall read of what this case needs")
    proposals: list[ProposedAction] = Field(min_length=1, max_length=4, description="Your first choice first")


SYSTEM_PROMPT = """\
You plan how to recover revenue at risk for an Egyptian e-commerce marketplace where most orders are \
cash on delivery (COD).

You get one case, the investigator's findings, the actions available for this case, and the store's \
policies. Propose 1 to 4 different actions, your first choice first.

Rules:
- Use only the listed actions, with parameters inside the listed ranges.
- Follow the policies. Do not propose an action a policy forbids for this case.
- For each action, cite the rule ids (like COD-2) that support or limit it.
- Some actions are escalation steps that only run after an earlier action has gone unanswered. If \
you propose one, also propose the earlier action: together they form the plan.
- Base your reasoning on the findings. Do not invent facts.
- You do not decide whether an action runs automatically or needs approval, and you do not estimate \
probabilities: the service calculates both after you answer. Just propose what would work best.
- Finish by calling submit_strategy exactly once.
"""


def _param_line(p: Param) -> str:
    if p.kind == "choice":
        allowed = " | ".join(p.choices)
    else:
        low = p.min if p.min is not None else "-"
        high = p.max if p.max is not None else ("value at risk" if p.at_most_value_at_risk else "-")
        allowed = f"{p.kind} {low}..{high}"
    return f"      - {p.name} ({allowed}{'' if p.required else ', optional'}): {p.description}"


def build_prompt(
    case: Case, actions: list[ActionSpec], policies: list[Policy], done: Mapping[str, float] | None = None
) -> str:
    report = (case.investigation or {}).get("report", {})
    lines = [
        f"Case type: {case.case_type}",
        f"Title: {case.title}",
        f"Subject: {case.subject_type} {case.subject_id}",
        f"Value at risk: {case.value_at_risk} {case.currency}",
        f"Detector signals: {json.dumps(case.signals, ensure_ascii=False, default=str)}",
        "",
        f"Investigation findings: {json.dumps(report, ensure_ascii=False)}",
        "",
        "Available actions:",
    ]
    for a in actions:
        lines.append(f"  - {a.key}: {a.description}")
        step = step_for(a.key, case.case_type)
        if step and step.after in {x.key for x in actions}:
            lines.append(f"      (escalation: only after {step.after} has gone unanswered for {step.wait_hours:g}h)")
        lines.extend(_param_line(p) for p in a.params)
    lines.append("")
    if done:
        lines.append("Already carried out (do not propose these again):")
        lines.extend(f"  - {a}, {h:.0f} hours ago" for a, h in sorted(done.items(), key=lambda kv: kv[1]))
        lines.append("")
    lines.append("Policies:")
    for p in policies:
        lines.append(p.text)
        lines.append("")
    return "\n".join(lines)


def make_check(
    case_type: str,
    actions: list[ActionSpec],
    policies: list[Policy],
    value_at_risk: Decimal,
    done: frozenset[str] = frozenset(),
) -> Callable[[Strategy, list[ToolCall]], None]:
    by_key = {a.key: a for a in actions}
    rules = {r for p in policies for r in p.rules}

    def check(strategy: Strategy, calls: list[ToolCall]) -> None:
        seen: set[str] = set()
        for proposal in strategy.proposals:
            spec = by_key.get(proposal.action)
            if spec is None:
                raise ValueError(f"{proposal.action!r} is not an available action. Choose from: {', '.join(by_key)}")
            params = spec.check_params(proposal.params, value_at_risk)
            unknown = sorted(set(proposal.policy_refs) - rules)
            if unknown:
                raise ValueError(f"{proposal.action} cites unknown rules {', '.join(unknown)}")
            key = json.dumps([proposal.action, params], sort_keys=True, default=str)
            if key in seen:
                raise ValueError(f"{proposal.action} is proposed twice with the same parameters")
            seen.add(key)
        proposed = {p.action for p in strategy.proposals}
        for action in proposed:
            step = step_for(action, case_type)
            if step and step.after in by_key and step.after not in proposed | done:
                raise ValueError(f"{action} is an escalation step after {step.after}: propose {step.after} as well")

    return check


def plan(
    case: Case,
    actions: list[ActionSpec],
    policies: list[Policy],
    llm: LLMClient,
    done: Mapping[str, float] | None = None,
) -> AgentResult[Strategy]:
    return run_agent(
        llm,
        system=SYSTEM_PROMPT,
        prompt=build_prompt(case, actions, policies, done),
        tools=[],
        submit=Submit(
            name="submit_strategy",
            description="Submit your proposed actions. Call exactly once.",
            schema=Strategy,
            check=make_check(case.case_type, actions, policies, case.value_at_risk, frozenset(done or {})),
        ),
        max_turns=4,
    )
