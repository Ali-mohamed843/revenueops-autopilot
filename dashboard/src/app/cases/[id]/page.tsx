import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AuditTrail, ExecutionItem, InvestigationPanel, WhyPanel } from "@/components/case-parts";
import { CaseStepButton } from "@/components/controls";
import { ButtonLink, Card, CardTitle, CaseStatusText, EmptyState, Money } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { CASE_STATUSES, CASE_TYPES, relativeTime, shortId } from "@/lib/format";
import type { CaseDetail } from "@/lib/types";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: `Case ${shortId(id)}` };
}

const STEPS = ["Detected", "Investigated", "Planned", "Action", "Closed"];

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let c: CaseDetail;
  try {
    c = await api.case(id);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
  const policies = await api.policies();
  const rules = Object.fromEntries(policies.flatMap((p) => p.rules.map((r) => [r.id, r.text])));
  const stage = CASE_STATUSES[c.status].stage;
  const waitingOnPerson = c.executions.some((e) => e.status === "pending_approval" || e.status === "awaiting_human");
  const nextStep =
    c.status === "open" || c.status === "investigation_failed"
      ? "investigate"
      : c.status === "investigated" || c.status === "planning_failed"
        ? "plan"
        : (c.status === "planned" || c.status === "acted") && c.next_action_id
          ? "act"
          : null;

  return (
    <>
      <Link href="/cases" className="self-start text-[13px] text-muted hover:text-ink">
        ← All cases
      </Link>

      <div className="mt-2 flex flex-wrap items-end justify-between gap-7">
        <div className="min-w-0 flex-[1_1_560px]">
          <p className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
            <span className="text-ink-2">{CASE_TYPES[c.case_type].label}</span>
            <CaseStatusText status={c.status} />
          </p>
          <h1 className="serif mt-3.5 text-[30px] leading-[1.15] font-normal tracking-[-0.01em] sm:text-[38px]">{c.title}</h1>
          <p className="mt-3 text-[13px] text-muted">
            {CASE_TYPES[c.case_type].blurb} · {c.subject_type} <span className="font-mono text-ink-2">{c.subject_id}</span>
          </p>
        </div>
        <div className="flex flex-col items-end gap-4">
          <div className="text-end">
            <p className="text-[13px] text-muted">At risk</p>
            <Money value={c.value_at_risk} size="large" className="mt-1.5" />
          </div>
          {nextStep && <CaseStepButton step={nextStep} caseId={c.id} />}
          {!nextStep && waitingOnPerson && (
            <ButtonLink href="#actions" variant="amber">
              Review the waiting action
            </ButtonLink>
          )}
          {!nextStep && !waitingOnPerson && c.status !== "closed" && c.plan && (
            <p className="max-w-64 text-end text-[13px] text-muted">The next step waits for the last one to have time to work.</p>
          )}
        </div>
      </div>

      <ol aria-label="Progress" className="mt-2 grid grid-cols-5 gap-2">
        {STEPS.map((label, i) => {
          const closed = c.status === "closed";
          const done = i < stage || (closed && i <= stage);
          const current = i === stage && !closed;
          const waiting = current && c.status === "acting";
          const color = waiting ? "var(--warn-mark)" : done || current ? "var(--good-mark)" : "var(--line)";
          return (
            <li key={label} className="min-w-0" aria-current={current ? "step" : undefined}>
              <div className="h-0.5" style={{ background: color }} />
              <p
                className={`mt-2.5 truncate text-[13px] ${waiting ? "text-warn" : done || current ? "text-ink" : "text-muted"}`}
              >
                {i === 3 && waiting ? "Action · waiting for you" : i === 4 && closed && c.close_reason ? `Closed · ${c.close_reason.replaceAll("_", " ")}` : label}
              </p>
            </li>
          );
        })}
      </ol>

      <div className="mt-2 flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[999_1_640px] flex-col gap-5">
          {c.plan && c.actions.length > 0 ? (
            <WhyPanel c={c} rules={rules} />
          ) : (
            <Card>
              <EmptyState title={c.investigation ? "Not planned yet" : "Not investigated yet"}>
                {c.investigation
                  ? "Plan the case to see the proposed actions, their scores and who may carry them out."
                  : "The Investigator reads the order and the customer’s history, then explains what is going on."}
              </EmptyState>
            </Card>
          )}
          <InvestigationPanel c={c} />
        </div>

        <aside className="flex min-w-0 flex-[1_1_360px] flex-col gap-5">
          <Card id="actions" className="scroll-mt-6 px-7 pt-[26px] pb-3">
            <CardTitle title="Actions" subtitle="Newest first" />
            <div className="mt-4">
              {c.executions.length ? (
                [...c.executions].reverse().map((e) => <ExecutionItem key={e.id} e={e} rules={rules} />)
              ) : (
                <p className="border-t border-line py-4 text-[13px] text-muted">
                  {c.status === "planned" ? "Planned and ready: take the next action." : "Nothing done yet."}
                </p>
              )}
            </div>
          </Card>

          <Card className="px-7 py-[26px]">
            <CardTitle title="Audit trail" subtitle="Every step, who took it, and when" />
            <div className="mt-[18px]">
              <AuditTrail events={c.events} />
            </div>
          </Card>

          <p className="px-1 text-[12px] text-muted">
            Detected {relativeTime(c.detected_at)} · last seen {relativeTime(c.last_seen_at)} · priority {c.priority}
            <span className="sr-only"> · stage {CASE_STATUSES[c.status].label}</span>
          </p>
        </aside>
      </div>
    </>
  );
}
