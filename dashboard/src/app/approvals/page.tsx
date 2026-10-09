import type { Metadata } from "next";
import Link from "next/link";

import { ApprovalControls, TaskControls } from "@/components/controls";
import { Card, EmptyState, PageTitle, RuleChip } from "@/components/ui";
import { api } from "@/lib/api";
import { casesById, needsPersonReasons } from "@/lib/cases";
import { CASE_TYPES, actionLabel, age, count, money, moneyCompact, paramsText, percent, relativeTime, toNumber } from "@/lib/format";
import type { CaseDetail, Execution } from "@/lib/types";

export const metadata: Metadata = { title: "Approvals" };

export default async function ApprovalsPage() {
  const [waiting, policies] = await Promise.all([
    api.executions({ status: ["pending_approval", "awaiting_human"], limit: 200 }),
    api.policies(),
  ]);
  const rules = Object.fromEntries(policies.flatMap((p) => p.rules.map((r) => [r.id, r.text])));
  const cases = await casesById(waiting.map((e) => e.case_id));
  const approvals = waiting.filter((e) => e.status === "pending_approval");
  const tasks = waiting.filter((e) => e.status === "awaiting_human");
  const inBalance = [...new Set(waiting.map((e) => e.case_id))].reduce((a, id) => a + toNumber(cases[id]?.value_at_risk), 0);
  const oldest = waiting.at(-1);

  return (
    <>
      <PageTitle
        title="Approvals"
        subtitle="Actions the decision engine won’t run on its own. Approve or reject them, and report back on tasks only a person can do."
        side={
          waiting.length > 0 && (
            <div className="flex gap-10">
              <div>
                <p className="text-[13px] text-muted">In the balance</p>
                <p className="serif num mt-1 text-[30px] font-light">
                  {moneyCompact(inBalance, "")} <span className="font-sans text-[13px] text-muted">EGP</span>
                </p>
              </div>
              <div>
                <p className="text-[13px] text-muted">Oldest request</p>
                <p className="serif num mt-1 text-[30px] font-light">{age(oldest?.requested_at)}</p>
              </div>
            </div>
          )
        }
      />

      {waiting.length === 0 ? (
        <Card className="mt-5">
          <EmptyState title="Nothing is waiting for you">
            Actions that need approval, or a person’s hands, show up here.
          </EmptyState>
        </Card>
      ) : (
        <div className="mt-5 flex flex-col gap-10">
          {approvals.length > 0 && (
            <section aria-labelledby="h-approve" className="flex flex-col gap-4">
              <SectionTitle id="h-approve" title="To approve" n={approvals.length} />
              <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,560px),1fr))] gap-5">
                {approvals.map((e) => (
                  <ApprovalCard key={e.id} e={e} c={cases[e.case_id]} rules={rules} />
                ))}
              </div>
            </section>
          )}
          {tasks.length > 0 && (
            <section aria-labelledby="h-tasks" className="flex flex-col gap-4">
              <SectionTitle id="h-tasks" title="Tasks for a person" n={tasks.length} />
              <Card className="overflow-hidden">
                {tasks.map((e, i) => (
                  <TaskRow key={e.id} e={e} c={cases[e.case_id]} rules={rules} first={i === 0} />
                ))}
              </Card>
            </section>
          )}
        </div>
      )}
    </>
  );
}

function SectionTitle({ id, title, n }: { id: string; title: string; n: number }) {
  return (
    <h2 id={id} className="flex items-center gap-2.5 text-[15px] font-medium">
      <span className="size-1.5 rounded-full bg-warn-mark" aria-hidden />
      {title} <span className="num text-[13px] text-muted">{count(n)}</span>
    </h2>
  );
}

function ApprovalCard({ e, c, rules }: { e: Execution; c: CaseDetail | undefined; rules: Record<string, string> }) {
  const { action, reasons } = needsPersonReasons(c, e.case_action_id);
  return (
    <article className="card flex flex-col gap-[22px] border-warn-line px-6 py-7 sm:px-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {c && <p className="text-[13px] text-muted">{CASE_TYPES[c.case_type].label}</p>}
          <h3 className="serif mt-1.5 text-[26px] leading-tight font-normal">{actionLabel(e.action_type)}</h3>
          <p className="mt-1.5 text-[13px] text-muted">
            {Object.keys(e.params).length > 0 && <>{paramsText(e.params)} · </>}requested {relativeTime(e.requested_at)}
          </p>
        </div>
        {c && (
          <div className="text-end">
            <p className="serif num text-[34px] leading-none font-light">{money(c.value_at_risk, "").split(".")[0]}</p>
            <p className="mt-1.5 text-[13px] text-muted">EGP at risk</p>
          </div>
        )}
      </div>

      {c && (
        <Link
          href={`/cases/${c.id}`}
          className="flex items-center gap-3 border-y border-line py-3 text-[13px] text-ink-2 hover:text-ink"
        >
          <span className="min-w-0 flex-1 truncate">
            {c.title} · {c.subject_type} {c.subject_id.slice(0, 8)}
          </span>
          <span className="text-ink">Open case</span>
        </Link>
      )}

      <dl className="grid grid-cols-1 gap-x-5 gap-y-3.5 sm:grid-cols-[120px_minmax(0,1fr)]">
        {action && (
          <>
            <dt className="text-[13px] text-muted">Why</dt>
            <dd className="serif text-[17px] leading-[1.55] font-light text-prose">{action.rationale}</dd>
          </>
        )}
        {reasons.length > 0 && (
          <>
            <dt className="text-[13px] text-muted">Why it needs you</dt>
            <dd className="space-y-1 text-[13px] leading-[1.55] text-ink-2">
              {reasons.map((r) => (
                <p key={r.text}>
                  {r.text} {r.rule && <RuleChip id={r.rule} text={rules[r.rule]} />}
                </p>
              ))}
            </dd>
          </>
        )}
        {action && (
          <>
            <dt className="text-[13px] text-muted">Expected gain</dt>
            <dd className="num text-good">
              +{moneyCompact(action.expected_value)}
              {e.confidence != null && <span className="ms-2.5 text-[13px] text-muted">{percent(e.confidence)} confidence</span>}
            </dd>
          </>
        )}
      </dl>

      <div className="pt-1">
        <ApprovalControls executionId={e.id} />
      </div>
    </article>
  );
}

function TaskRow({
  e,
  c,
  rules,
  first,
}: {
  e: Execution;
  c: CaseDetail | undefined;
  rules: Record<string, string>;
  first: boolean;
}) {
  const { action, reasons } = needsPersonReasons(c, e.case_action_id);
  const why = reasons[0];
  return (
    <div className={`flex flex-wrap items-center gap-x-7 gap-y-4 px-7 py-[22px] ${first ? "" : "border-t border-line-2"}`}>
      <div className="min-w-0 flex-[1_1_360px]">
        <p className="text-[15px] font-medium">{actionLabel(e.action_type)}</p>
        <p className="mt-1 text-[13px] leading-normal text-ink-2">
          {c && (
            <Link href={`/cases/${c.id}`} className="hover:text-ink">
              {c.title}.
            </Link>
          )}{" "}
          {why ? why.text : action?.rationale} {why?.rule && <RuleChip id={why.rule} text={rules[why.rule]} />}
        </p>
      </div>
      {c && (
        <p className="serif num text-xl">
          {money(c.value_at_risk, "").split(".")[0]} <span className="font-sans text-[13px] text-muted">EGP</span>
        </p>
      )}
      <TaskControls executionId={e.id} />
    </div>
  );
}
