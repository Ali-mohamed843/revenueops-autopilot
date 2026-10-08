"""Command line: `revenueops detect` and `revenueops investigate`."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from revenueops.adapters.base import StoreError
from revenueops.adapters.registry import build_adapter
from revenueops.agents.llm import build_llm
from revenueops.cases.models import OPEN_STATUSES, Case
from revenueops.config import get_settings
from revenueops.db import get_engine, session_factory
from revenueops.pipeline import cases_to_investigate, detect, investigate_cases


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
        if case_id:
            case = session.get(Case, uuid.UUID(case_id))
            if case is None:
                print(f"No case {case_id}", file=sys.stderr)
                return 1
            cases = [case]
        else:
            cases = cases_to_investigate(session, store.name, limit)
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
        if run.stopped:
            print(f"Stopped early: {run.stopped}")
            print("  Failed and unstarted cases are retried next run. Wait a few minutes, or set AGENT_MODEL")
            print("  to another model (paid models are rarely rate-limited).")
    return 1 if run.failed and not run.investigated else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="revenueops")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="Scan the store and open, update or close cases")
    inv = sub.add_parser("investigate", help="Run the Investigator on open cases")
    inv.add_argument("--limit", type=int, default=3, help="How many cases (most urgent first)")
    inv.add_argument("--case", dest="case_id", help="Investigate one case by id")
    args = parser.parse_args(argv)

    # Setup and connectivity problems get one readable line, not a traceback.
    try:
        if args.command == "detect":
            return cmd_detect()
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
