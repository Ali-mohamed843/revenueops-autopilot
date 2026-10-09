"""Numbers for the dashboard. Every figure has a plain definition, kept next to its query.

- Open: cases not closed, whatever stage they are in.
- Recovered: cases closed because their problem cleared *after* at least one action succeeded.
  This is attribution by sequence, not proof of cause; the dashboard says so.
- Cleared on their own: cases closed because their problem cleared with no action taken.
- Actions: executions by how they were decided (auto, approved by a person, done by a person).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.cases.models import OPEN_STATUSES, Case, CaseStatus
from revenueops.executor.models import Execution, ExecutionStatus

TOP_GOVERNORATES = 8


@dataclass
class Bucket:
    cases: int = 0
    value: Decimal = Decimal(0)

    def add(self, value: Decimal) -> None:
        self.cases += 1
        self.value += value

    def out(self) -> dict[str, Any]:
        return {"cases": self.cases, "value": str(self.value.quantize(Decimal("0.01")))}


def _day(at: datetime) -> date:
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC).date()


def overview(session: Session, days: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    start = (now - timedelta(days=days - 1)).date()

    end = now.date()

    def in_range(at: datetime | None) -> bool:
        # Bounded on both sides: a timestamp after `now` (clock skew, a test clock) is out of range.
        return at is not None and start <= _day(at) <= end

    cases = list(session.scalars(select(Case)))
    executions = list(session.scalars(select(Execution)))
    acted_cases = {e.case_id for e in executions if e.status == ExecutionStatus.SUCCEEDED}

    open_ = Bucket()
    by_type: dict[str, Bucket] = defaultdict(Bucket)
    by_governorate: dict[str, Bucket] = defaultdict(Bucket)
    by_status: Counter[str] = Counter()
    risk_scores: Counter[int] = Counter()
    recovered, cleared = Bucket(), Bucket()
    daily: dict[date, dict[str, Any]] = {
        start + timedelta(days=i): {
            "detected_cases": 0,
            "detected_value": Decimal(0),
            "recovered_value": Decimal(0),
            "auto": 0,
            "approved": 0,
            "by_person": 0,
        }
        for i in range(days)
    }
    tokens = {"input_tokens": 0, "output_tokens": 0, "agent_runs": 0}

    for c in cases:
        for run in (c.investigation, c.plan):
            if run and run.get("usage"):
                tokens["input_tokens"] += run["usage"].get("input_tokens", 0)
                tokens["output_tokens"] += run["usage"].get("output_tokens", 0)
                tokens["agent_runs"] += 1
        if in_range(c.detected_at):
            day = daily[_day(c.detected_at)]
            day["detected_cases"] += 1
            day["detected_value"] += c.value_at_risk
        if c.status in OPEN_STATUSES:
            open_.add(c.value_at_risk)
            by_type[c.case_type].add(c.value_at_risk)
            by_status[c.status] += 1
            governorate = c.signals.get("governorate")
            if governorate:
                by_governorate[str(governorate)].add(c.value_at_risk)
            score = c.signals.get("risk_score")
            if isinstance(score, int):
                risk_scores[min(score // 10, 9)] += 1
        elif c.status == CaseStatus.CLOSED and c.close_reason == "condition_cleared" and in_range(c.closed_at):
            if c.id in acted_cases:
                recovered.add(c.value_at_risk)
                assert c.closed_at is not None
                daily[_day(c.closed_at)]["recovered_value"] += c.value_at_risk
            else:
                cleared.add(c.value_at_risk)

    actions: Counter[str] = Counter()
    for e in executions:
        if e.status in (ExecutionStatus.PENDING_APPROVAL, ExecutionStatus.AWAITING_HUMAN):
            actions[e.status] += 1  # waiting now, whatever the range
            continue
        if not in_range(e.finished_at or e.decided_at or e.requested_at):
            continue
        if e.status == ExecutionStatus.SUCCEEDED or e.status == ExecutionStatus.ROLLED_BACK:
            how = "auto" if e.decided_by == "auto" else ("by_person" if e.tier == "human_only" else "approved")
            actions[how] += 1
            if e.status == ExecutionStatus.ROLLED_BACK:
                actions["rolled_back"] += 1
            finished = e.finished_at or e.requested_at
            daily[_day(finished)][how] += 1
        else:
            actions[e.status] += 1  # rejected, failed

    decided = actions["auto"] + actions["approved"] + actions["by_person"]
    ranked_governorates = sorted(by_governorate.items(), key=lambda kv: -kv[1].value)
    return {
        "currency": "EGP",
        "days": days,
        "from": start.isoformat(),
        "to": now.date().isoformat(),
        "open": open_.out(),
        "recovered": recovered.out(),
        "cleared_without_action": cleared.out(),
        "actions": {
            k: actions[k]
            for k in (
                "auto",
                "approved",
                "by_person",
                "rejected",
                "failed",
                "rolled_back",
                ExecutionStatus.PENDING_APPROVAL,
                ExecutionStatus.AWAITING_HUMAN,
            )
        },
        "automation_rate": round(actions["auto"] / decided, 4) if decided else None,
        "by_type": [{"case_type": t, **b.out()} for t, b in sorted(by_type.items(), key=lambda kv: -kv[1].value)],
        "by_status": [{"status": s, "cases": by_status[s]} for s in OPEN_STATUSES],
        "by_governorate": [{"governorate": g, **b.out()} for g, b in ranked_governorates[:TOP_GOVERNORATES]],
        "other_governorates": Bucket(
            sum(b.cases for _, b in ranked_governorates[TOP_GOVERNORATES:]),
            sum((b.value for _, b in ranked_governorates[TOP_GOVERNORATES:]), Decimal(0)),
        ).out(),
        "risk_scores": [
            {"from": i * 10, "to": i * 10 + 9 if i < 9 else 100, "cases": risk_scores[i]} for i in range(10)
        ],
        "daily": [
            {
                "date": d.isoformat(),
                **{k: (str(v.quantize(Decimal("0.01"))) if isinstance(v, Decimal) else v) for k, v in row.items()},
            }
            for d, row in daily.items()
        ],
        "llm": tokens,
    }
