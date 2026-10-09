"""Command line: detect, investigate, plan, act, then queue / approve / reject / complete / rollback."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from revenueops.adapters.base import StoreError
from revenueops.adapters.registry import build_adapter
from revenueops.agents.llm import build_llm
from revenueops.cases.models import OPEN_STATUSES, Case
from revenueops.config import get_settings
from revenueops.db import get_engine, session_factory
from revenueops.executor import engine
from revenueops.executor.models import OPEN_EXECUTION, Execution
from revenueops.pipeline import cases_to_investigate, cases_to_plan, detect, investigate_cases, plan_cases
from revenueops.simulation import outcomes as sim
from revenueops.simulation.dry_run import dry_run


def cmd_detect() -> int:
    settings = get_settings()
    store = build_adapter(settings)
    with session_factory(get_engine())() as session:
        run = detect(session, store)
        open_by_type = Counter(c.case_type for c in session.scalars(select(Case).where(Case.status.in_(OPEN_STATUSES))))
    s = run.sync
    print(f"Detected {len(run.result.candidates)} cases on {store.name}")
    print(f"  opened {s.opened}, updated {s.updated}, reclassified {s.reclassified}, closed {s.closed}")
    for skipped_type, missing in run.result.skipped.items():
        print(f"  skipped {skipped_type}: store lacks {', '.join(missing)}")
    print("Open cases by type:")
    for case_type, n in sorted(open_by_type.items()):
        print(f"  {case_type:<22} {n}")
    return 0


def cmd_investigate(limit: int, case_id: str | None) -> int:
    settings = get_settings()
    store = build_adapter(settings)
    llm = build_llm(settings)
    with session_factory(get_engine())() as session:
        cases = [_get_case(session, case_id)] if case_id else cases_to_investigate(session, store.name, limit)
        print(f"Investigating {len(cases)} case(s) with {llm.model}")
        run = investigate_cases(session, store, llm, cases)
        done = set(run.investigated) | set(run.failed)
        for case in cases:
            if case.id not in done:
                continue
            if case.id in run.failed:
                print(f"  FAILED {case.case_type} {case.subject_id}: {run.failed[case.id]}")
            else:
                report = (case.investigation or {}).get("report", {})
                print(f"  {case.case_type} {case.subject_id} ({case.value_at_risk} {case.currency})")
                print(f"    {report.get('summary', '')}")
        _print_stopped(run.stopped)
    return 1 if run.failed and not run.investigated else 0


def cmd_plan(limit: int, case_id: str | None) -> int:
    settings = get_settings()
    store = build_adapter(settings)
    llm = build_llm(settings)
    with session_factory(get_engine())() as session:
        cases = [_get_case(session, case_id)] if case_id else cases_to_plan(session, store.name, limit)
        if not cases:
            print("No investigated cases to plan. Run `revenueops investigate` first.")
            return 0
        print(f"Planning {len(cases)} case(s) with {llm.model}")
        run = plan_cases(session, store, llm, cases)
        for case in cases:
            if case.id in run.failed:
                print(f"  FAILED {case.case_type} {case.subject_id}: {run.failed[case.id]}")
            elif case.id in run.planned:
                print(f"  {case.case_type} {case.subject_id} ({case.value_at_risk} {case.currency})")
                for a in (a for a in case.actions if a.status == "proposed"):
                    mark = "*" if a.recommended else " "
                    print(
                        f"   {mark}{a.rank}. {a.action_type:<28} {a.tier:<11} EV {a.expected_value:>9} EGP  {a.params}"
                    )
                    if not a.ready:
                        print(f"        later: {a.waiting_for}")
        _print_stopped(run.stopped)
    return 1 if run.failed and not run.planned else 0


def cmd_act(limit: int, case_id: str | None) -> int:
    settings = get_settings()
    store = build_adapter(settings)
    write = engine.messenger_writer(build_llm(settings))
    with session_factory(get_engine())() as session:
        cases = [_get_case(session, case_id)] if case_id else engine.cases_to_act(session, store.name, limit)
        if not cases:
            print("No planned cases. Run `revenueops plan` first.")
            return 0
        run = engine.act(session, store, write, cases)
        for ex_id in run.ran + run.queued + run.failed:
            ex = session.get(Execution, ex_id)
            assert ex is not None
            print(f"  {ex.status:<17} {ex.action_type:<28} {ex.tier:<11} case {ex.case_id}  execution {ex.id}")
            if ex.error:
                print(f"      error: {ex.error}")
            for m in ex.messages:
                print(f"      outbox -> {m.channel} {m.recipient}: {m.body}")
        counts = f"Ran {len(run.ran)}, queued {len(run.queued)}, failed {len(run.failed)}"
        print(f"{counts}; {len(run.waiting)} case(s) waiting for their next step")
    return 1 if run.failed and not (run.ran or run.queued) else 0


def cmd_queue() -> int:
    with session_factory(get_engine())() as session:
        waiting = list(
            session.scalars(
                select(Execution).where(Execution.status.in_(OPEN_EXECUTION)).order_by(Execution.requested_at)
            )
        )
        if not waiting:
            print("Nothing is waiting for a person.")
        for ex in waiting:
            case = ex.case
            print(
                f"{ex.id}  {ex.status:<17} {ex.action_type:<28} {case.case_type} {case.value_at_risk} {case.currency}"
            )
            print(f"    {ex.action.rationale}")
    return 0


def cmd_decide(command: str, execution_id: str, by: str, note: str | None, failed: bool) -> int:
    settings = get_settings()
    with session_factory(get_engine())() as session:
        ex_id = uuid.UUID(execution_id)
        try:
            if command == "approve":
                store = build_adapter(settings)
                ex = engine.approve(session, store, engine.messenger_writer(build_llm(settings)), ex_id, by, note)
            elif command == "reject":
                ex = engine.reject(session, ex_id, by, note)
            elif command == "complete":
                ex = engine.complete(session, ex_id, by, not failed, note or "")
            else:
                ex = engine.rollback(session, build_adapter(settings), ex_id, by, note or "")
        except engine.ExecutorError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
        print(f"{ex.action_type}: {ex.status}")
        if ex.error:
            print(f"  error: {ex.error}")
        for m in ex.messages:
            print(f"  outbox ({m.status}) -> {m.channel} {m.recipient}: {m.body}")
    return 0


def cmd_simulate(what: str, episodes: int, seed: int) -> int:
    settings = get_settings()
    with session_factory(get_engine())() as session:
        try:
            if what == "dry-run":
                rates, _ = sim.active_rates(session)
                run = dry_run(session, build_adapter(settings), rates, rates.source if rates else None)
                r = run.report
                print(
                    f"Dry run over {r['cases']} cases ({r['value_at_risk']} EGP at risk), using {run.params['rates']}"
                )
                print(f"  would run on its own: {r['tiers']['auto']}")
                print(f"  needs approval:       {r['tiers']['approval']}")
                print(f"  needs a person:       {r['tiers']['human_only']}")
                print(f"  already waiting:      {r['tiers']['waiting']}")
                print(f"  nothing ready:        {r['tiers']['nothing_ready']}")
                print(f"  expected recovery:    {r['expected_recovery']} EGP")
                for action, n in r["actions"].items():
                    print(f"    {n:>4}  {action}")
                print(f"  high-risk actions: {r['high_risk_total']} (nothing was executed)")
            elif what == "outcomes":
                run = sim.simulate_outcomes(session, episodes, seed)
                r = run.report
                print(f"Simulated {r['episodes']} outcomes (seed {seed}): {r['recovered']} recovered")
                print(f"{'case type':<20} {'action':<28} {'assumed':>8} {'measured':>9}  95% interval     n")
                for row in r["comparison"]:
                    if row["trials"]:
                        print(
                            f"{row['case_type']:<20} {row['action']:<28} {row['assumed']:>8.2f} {row['measured']:>9.2f}"
                            f"  {row['low']:.2f}-{row['high']:.2f}  {row['trials']:>5}"
                        )
                print("Run `revenueops simulate calibrate` to use these rates in scoring.")
            else:
                run = sim.calibrate(session)
                print(
                    f"Calibration {str(run.id)[:8]}: {run.report['usable']} rates with enough trials now drive scoring."
                )
        except sim.SimulationError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
    return 0


def _get_case(session: Session, case_id: str) -> Case:
    case = session.get(Case, uuid.UUID(case_id))
    if case is None:
        raise ValueError(f"No case {case_id}")
    return case


def _print_stopped(reason: str | None) -> None:
    if reason:
        print(f"Stopped early: {reason}")
        print("  Failed and unstarted cases are retried next run. Wait a few minutes, or set AGENT_MODEL")
        print("  to another model (paid models are rarely rate-limited).")


def main(argv: list[str] | None = None) -> int:
    # Messages are Arabic; a Windows console or a pipe may default to a code page that can't print them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="revenueops")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="Scan the store and open, update or close cases")
    inv = sub.add_parser("investigate", help="Run the Investigator on open cases")
    inv.add_argument("--limit", type=int, default=3, help="How many cases (most urgent first)")
    inv.add_argument("--case", dest="case_id", help="Investigate one case by id")
    pln = sub.add_parser("plan", help="Run the Strategist on investigated cases and score its proposals")
    pln.add_argument("--limit", type=int, default=3, help="How many cases (most urgent first)")
    pln.add_argument("--case", dest="case_id", help="Plan one case by id")
    act = sub.add_parser("act", help="Run each planned case's next action, or queue it for a person")
    act.add_argument("--limit", type=int, default=10, help="How many cases (most urgent first)")
    act.add_argument("--case", dest="case_id", help="Act on one case by id")
    sub.add_parser("queue", help="List actions waiting for approval or for a person")
    simulate = sub.add_parser("simulate", help="Dry run, simulated outcomes, or calibration")
    simulate.add_argument("what", choices=["dry-run", "outcomes", "calibrate"])
    simulate.add_argument("--episodes", type=int, default=5000, help="For outcomes: how many simulated cases")
    simulate.add_argument("--seed", type=int, default=7, help="For outcomes: the random seed (same seed, same run)")
    for name, help_ in (
        ("approve", "Approve a waiting action and run it"),
        ("reject", "Reject a waiting action"),
        ("complete", "Report back on an action a person carried out"),
        ("rollback", "Undo an action"),
    ):
        cmd = sub.add_parser(name, help=help_)
        cmd.add_argument("execution_id")
        cmd.add_argument("--by", required=True, help="Your name, for the audit trail")
        cmd.add_argument("--note", required=name in ("complete", "rollback"), help="Why, or what happened")
        if name == "complete":
            cmd.add_argument("--failed", action="store_true", help="The action didn't work")
    args = parser.parse_args(argv)

    # Setup and connectivity problems get one readable line, not a traceback.
    try:
        if args.command == "detect":
            return cmd_detect()
        if args.command == "plan":
            return cmd_plan(args.limit, args.case_id)
        if args.command == "act":
            return cmd_act(args.limit, args.case_id)
        if args.command == "queue":
            return cmd_queue()
        if args.command == "simulate":
            return cmd_simulate(args.what, args.episodes, args.seed)
        if args.command in ("approve", "reject", "complete", "rollback"):
            return cmd_decide(args.command, args.execution_id, args.by, args.note, getattr(args, "failed", False))
        return cmd_investigate(args.limit, args.case_id)
    except ValueError as e:  # missing or invalid configuration
        print(f"error: {e}", file=sys.stderr)
    except StoreError as e:
        print(f"error: {e}\n  Is StoreForge running, with INTEGRATION_API_KEY set?", file=sys.stderr)
    except OperationalError as e:
        print(
            f"error: cannot reach the database: {e.orig}\n  Is Postgres running (docker compose up -d postgres)?",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
