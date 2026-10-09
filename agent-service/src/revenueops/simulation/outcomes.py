"""Simulated outcomes, measured success rates, and calibrations the scorer can use.

The experiment is randomised: each simulated case gets an action chosen at random from the catalogue
actions for its type, or no action (the control group). Random assignment is what makes the measured
uplift (with vs without) unbiased; the agents' own choices would favour easy cases and inflate it.
"""

from __future__ import annotations

import random
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from revenueops.cases.models import Case
from revenueops.decision import priors
from revenueops.decision.catalogue import CATALOGUE
from revenueops.decision.rates import MIN_TRIALS, NO_ACTION, Rate, RateTable
from revenueops.simulation import world
from revenueops.simulation.models import Outcome, RunKind, SimulationRun


class SimulationError(Exception):
    """The simulation can't run (no cases to model, nothing to calibrate from)."""


@dataclass(frozen=True)
class Profile:
    case_type: str
    value: Decimal
    segment: str


def profiles(session: Session) -> list[Profile]:
    """The store's real case mix: types, values and segments, open or closed."""
    return [
        Profile(c.case_type, c.value_at_risk, world.segment(c.case_type, c.value_at_risk, c.signals))
        for c in session.scalars(select(Case))
    ]


def arms(case_type: str) -> list[str]:
    return [NO_ACTION, *sorted(k for k, a in CATALOGUE.items() if case_type in a.case_types)]


def simulate_outcomes(session: Session, episodes: int, seed: int) -> SimulationRun:
    if episodes < 1:
        raise SimulationError("Run at least one episode")
    mix = profiles(session)
    if not mix:
        raise SimulationError("No cases to model the store on yet: scan the store first")
    rng = random.Random(seed)
    run = SimulationRun(id=uuid.uuid4(), kind=RunKind.OUTCOMES, seed=seed, params={"episodes": episodes})
    session.add(run)
    session.flush()

    rows: list[dict[str, Any]] = []
    for _ in range(episodes):
        p = rng.choice(mix)
        action = rng.choice(arms(p.case_type))
        rows.append(
            {
                "id": uuid.uuid4(),
                "run_id": run.id,
                "case_type": p.case_type,
                "action": action,
                "segment": p.segment,
                "value": p.value,
                "recovered": world.draw(rng, p.case_type, action, p.segment),
                "simulated": True,
            }
        )
    session.execute(insert(Outcome), rows)
    table = measure(session, run.id)
    run.report = {
        "episodes": episodes,
        "recovered": sum(r["recovered"] for r in rows),
        "case_types": sorted({r["case_type"] for r in rows}),
        "comparison": comparison(table),
    }
    session.commit()
    return run


def measure(session: Session, run_id: uuid.UUID) -> RateTable:
    """Success rates per (action, case type) from one outcomes run."""

    def counts(*where: Any) -> dict[tuple[str, str], int]:
        query = (
            select(Outcome.action, Outcome.case_type, func.count())
            .where(Outcome.run_id == run_id, *where)
            .group_by(Outcome.action, Outcome.case_type)
        )
        return {(a, ct): int(n) for a, ct, n in session.execute(query)}

    trials = counts()
    successes = counts(Outcome.recovered.is_(True))
    return RateTable(rates={k: Rate(successes.get(k, 0), n) for k, n in trials.items()})


def comparison(table: RateTable) -> list[dict[str, Any]]:
    """Assumed vs measured, for every catalogue action and every case type's baseline."""
    rows: list[dict[str, Any]] = []
    for case_type, base in priors.BASELINE.items():
        keys = [(NO_ACTION, base), *((k, p) for (k, ct), p in priors.WITH_ACTION.items() if ct == case_type)]
        for action, assumed in keys:
            rate = table.rates.get((action, case_type))
            lo, hi = rate.interval() if rate else (0.0, 1.0)
            rows.append(
                {
                    "case_type": case_type,
                    "action": action,
                    "assumed": assumed,
                    "measured": round(rate.p, 4) if rate else None,
                    "low": round(lo, 4),
                    "high": round(hi, 4),
                    "successes": rate.successes if rate else 0,
                    "trials": rate.trials if rate else 0,
                    "enough": bool(rate and rate.trials >= MIN_TRIALS),
                }
            )
    return rows


def calibrate(session: Session, outcomes_run_id: uuid.UUID | None = None) -> SimulationRun:
    """Freeze the rates from an outcomes run (the latest by default) as the scorer's new numbers."""
    source = (
        session.get(SimulationRun, outcomes_run_id)
        if outcomes_run_id
        else session.scalars(
            select(SimulationRun)
            .where(SimulationRun.kind == RunKind.OUTCOMES)
            .order_by(SimulationRun.created_at.desc())
        ).first()
    )
    if source is None or source.kind != RunKind.OUTCOMES:
        raise SimulationError("No outcomes run to calibrate from: simulate outcomes first")
    table = measure(session, source.id)
    usable = sum(1 for r in table.rates.values() if r.trials >= MIN_TRIALS)
    run = SimulationRun(
        id=uuid.uuid4(),
        kind=RunKind.CALIBRATION,
        seed=source.seed,
        params={"outcomes_run": str(source.id), "min_trials": MIN_TRIALS},
        report={
            "rates": [
                {"action": a, "case_type": ct, "successes": r.successes, "trials": r.trials}
                for (a, ct), r in sorted(table.rates.items())
            ],
            "usable": usable,
            "episodes": source.params.get("episodes"),
        },
    )
    session.add(run)
    session.commit()
    return run


def active_rates(session: Session) -> tuple[RateTable | None, SimulationRun | None]:
    """The latest calibration, as the scorer uses it. None: still on the starting estimates."""
    run = session.scalars(
        select(SimulationRun).where(SimulationRun.kind == RunKind.CALIBRATION).order_by(SimulationRun.created_at.desc())
    ).first()
    if run is None:
        return None, None
    rates = {(r["action"], r["case_type"]): Rate(r["successes"], r["trials"]) for r in run.report.get("rates", [])}
    source = f"calibration {str(run.id)[:8]}: {run.report.get('episodes')} simulated outcomes, seed {run.seed}"
    return RateTable(rates=rates, source=source), run


def by_segment(session: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    """Recovery per segment and action, for showing where an action works and where it doesn't."""
    counts: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for case_type, action, seg, recovered in session.execute(
        select(Outcome.case_type, Outcome.action, Outcome.segment, Outcome.recovered).where(Outcome.run_id == run_id)
    ):
        c = counts[(case_type, action, seg)]
        c[0] += int(recovered)
        c[1] += 1
    return [
        {"case_type": ct, "action": a, "segment": s, "successes": n[0], "trials": n[1]}
        for (ct, a, s), n in sorted(counts.items())
    ]
