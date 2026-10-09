import { cookies } from "next/headers";
import Link from "next/link";

import { Donut, LineChart, type Series } from "@/components/charts";
import { ScanButton } from "@/components/controls";
import {
  ButtonLink,
  Card,
  CardTitle,
  EmptyState,
  ExecutionStatusText,
  Money,
  Segmented,
  caseStatusStyle,
  cn,
} from "@/components/ui";
import { api } from "@/lib/api";
import { casesById, needsPersonReasons } from "@/lib/cases";
import {
  CASE_STATUSES,
  CASE_TYPES,
  actionLabel,
  count,
  greeting,
  longDate,
  moneyCompact,
  percent,
  relativeTime,
  shortDate,
  subjectLabel,
  toNumber,
} from "@/lib/format";
import type { CaseDetail, Execution, Stats } from "@/lib/types";

const RANGES = [7, 30, 90];
const TYPE_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];
const FLOW: Series[] = [
  { key: "detected", label: "Newly at risk", color: "var(--c2)" },
  { key: "recovered", label: "Recovered", color: "var(--c1)" },
];

export default async function Overview({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const requested = Number((await searchParams).days);
  const days = RANGES.includes(requested) ? requested : 30;
  const operator = (await cookies()).get("operator")?.value;
  const [s, waiting, recent] = await Promise.all([
    api.stats(days),
    api.executions({ status: ["pending_approval", "awaiting_human"], limit: 200 }),
    api.executions({ limit: 5 }),
  ]);
  const cases = await casesById([...waiting, ...recent].map((e) => e.case_id));

  const decided = s.actions.auto + s.actions.approved + s.actions.by_person;
  const newly = s.daily.reduce((a, d) => a + toNumber(d.detected_value), 0);
  const recovered = toNumber(s.recovered.value);
  const cleared = toNumber(s.cleared_without_action.value);
  const waitingValue = sumCaseValues(waiting, cases);
  const approvals = waiting.filter((e) => e.status === "pending_approval");
  const tasks = waiting.filter((e) => e.status === "awaiting_human");
  const first = waiting.at(-1); // the oldest request
  const firstWhy = first ? needsPersonReasons(cases[first.case_id], first.case_action_id).reasons[0] : undefined;
  const daily = s.daily.map((d) => ({ ...d, label: shortDate(d.date) }));
  const stages = s.by_status.filter((r) => r.cases > 0 || !r.status.endsWith("failed"));
  const openCases = s.open.cases;

  return (
    <>
      <div className="mb-5 flex flex-wrap items-end gap-4">
        <div className="min-w-0 flex-[1_1_420px]">
          <p className="mb-2.5 text-[13px] text-muted">{longDate()}</p>
          <h1 className="serif text-[34px] leading-[1.1] font-normal tracking-[-0.015em] sm:text-[44px]">
            {greeting()}
            {operator ? `, ${operator.split(" ")[0]}` : ""}
          </h1>
        </div>
        <Segmented
          label="Date range"
          items={RANGES.map((r) => ({ href: r === 30 ? "/" : `/?days=${r}`, label: `${r} days`, active: r === days }))}
        />
        <ScanButton />
      </div>

      {/* Hero + waiting */}
      <div className="flex flex-wrap gap-5">
        <Card className="flex min-w-0 flex-[1.7_1_600px] flex-col px-6 pt-8 pb-7 sm:px-10 sm:pt-9">
          <p className="text-[13px] text-muted">Revenue at risk now</p>
          <Money value={s.open.value} size="hero" className="mt-3.5" />
          <p className="mt-4 text-[15px] text-ink-2">
            Across {count(openCases)} open case{openCases === 1 ? "" : "s"}
          </p>
          <div className="mt-7 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-5 border-t border-line pt-5 lg:mt-auto">
            <Figure label={`Newly at risk, ${days} days`} value={moneyCompact(newly, "")} />
            <Figure label={`Recovered, ${days} days`} value={moneyCompact(recovered, "")} tone="good" />
            <Figure label="Ran on its own" value={percent(s.automation_rate)} />
          </div>
        </Card>

        <section
          aria-labelledby="wait-h"
          className="card flex min-w-0 flex-[1_1_360px] flex-col gap-4 border-warn-line bg-warn-soft p-8"
        >
          <h2 id="wait-h" className="flex items-center gap-2 text-[13px] font-medium text-warn-heading">
            <span className="size-1.5 rounded-full bg-warn-mark" aria-hidden />
            Waiting for you
          </h2>
          <p>
            <span className="serif num text-[64px] leading-none font-light">{waiting.length}</span>
            <span className="serif ms-2.5 text-[22px] text-ink-2">decision{waiting.length === 1 ? "" : "s"}</span>
          </p>
          {waiting.length > 0 ? (
            <>
              <p className="text-sm leading-normal text-ink-2">
                {count(approvals.length)} approval{approvals.length === 1 ? "" : "s"} and {count(tasks.length)} task
                {tasks.length === 1 ? "" : "s"}. <span className="num text-ink">{moneyCompact(waitingValue)}</span> depends
                on them.
              </p>
              {first && (
                <div className="border-t border-warn-divider pt-4">
                  <div className="flex justify-between gap-3">
                    <span className="font-medium">{actionLabel(first.action_type)}</span>
                    {cases[first.case_id] && (
                      <span className="num">{moneyCompact(cases[first.case_id].value_at_risk)}</span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
                    {cases[first.case_id] && <>{cases[first.case_id].title}. </>}
                    {firstWhy?.text}
                    {firstWhy?.rule && ` (${firstWhy.rule})`}
                  </p>
                </div>
              )}
              <ButtonLink href="/approvals" variant="amber" className="mt-auto self-start">
                Review the queue
              </ButtonLink>
            </>
          ) : (
            <p className="text-sm text-ink-2">Nothing needs a person right now. Automatic actions keep running.</p>
          )}
        </section>
      </div>

      {/* Brief */}
      <Card className="flex flex-wrap gap-x-12 gap-y-4 px-6 py-8 sm:px-10">
        <div className="flex-[0_0_200px]">
          <p className="font-medium">Brief</p>
          <p className="mt-1 text-[13px] text-muted">From the last {days} days’ numbers</p>
        </div>
        <p className="serif min-w-0 flex-[1_1_520px] text-[19px] leading-[1.55] font-light text-prose sm:text-[21px]">
          <Brief s={s} days={days} newly={newly} waiting={waiting.length} />
        </p>
      </Card>

      {/* Rings */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,380px),1fr))] gap-5">
        <Card className="flex flex-col gap-6 px-7 py-7">
          <CardTitle title="Where the risk is" subtitle="Open cases by type, EGP" />
          {s.by_type.length ? (
            <Donut
              label="Money at risk by case type"
              center={moneyCompact(s.open.value, "")}
              centerLabel="EGP"
              slices={s.by_type.map((t, i) => ({
                label: CASE_TYPES[t.case_type].label,
                value: toNumber(t.value),
                display: moneyCompact(t.value, ""),
                color: TYPE_COLORS[i % TYPE_COLORS.length],
                href: `/cases?type=${t.case_type}`,
              }))}
            />
          ) : (
            <EmptyState title="Nothing at risk">Scan the store to look for cases.</EmptyState>
          )}
        </Card>

        <Card className="flex flex-col gap-6 px-7 py-7">
          <CardTitle title="How actions were decided" subtitle={`${count(decided)} action${decided === 1 ? "" : "s"} in ${days} days`} />
          <Donut
            label="Actions by how they were decided"
            center={percent(s.automation_rate)}
            centerLabel="on its own"
            slices={[
              { label: "Automatic", value: s.actions.auto, display: count(s.actions.auto), color: "var(--c1)" },
              { label: "Approved by a person", value: s.actions.approved, display: count(s.actions.approved), color: "var(--c2)" },
              { label: "Done by a person", value: s.actions.by_person, display: count(s.actions.by_person), color: "var(--c6)" },
            ]}
            footer={
              <p className="border-t border-line-3 pt-2.5 text-[12px] text-muted">
                Also {count(s.actions.rejected)} rejected,{" "}
                <span className={cn(s.actions.failed > 0 && "text-bad")}>{count(s.actions.failed)} failed</span>,{" "}
                {count(s.actions.rolled_back)} undone
              </p>
            }
          />
        </Card>

        <Card className="flex flex-col gap-6 px-7 py-7">
          <CardTitle title="Recovery rate" subtitle="Of everything newly at risk" />
          <Donut
            label="Share of newly-at-risk revenue recovered"
            center={newly ? percent(recovered / newly) : "—"}
            centerLabel="recovered"
            slices={[
              { label: "After an action", value: recovered, display: moneyCompact(recovered, ""), color: "var(--c1)" },
              { label: "Cleared on its own", value: cleared, display: moneyCompact(cleared, ""), color: "var(--c4)" },
              {
                label: "Not recovered yet",
                value: Math.max(newly - recovered - cleared, 0),
                display: moneyCompact(Math.max(newly - recovered - cleared, 0), ""),
                color: "var(--track)",
              },
            ]}
            footer={
              <p className="pt-2.5 text-[12px] leading-normal text-muted">
                Shows the order of events, not proof that the action caused it.
              </p>
            }
          />
        </Card>
      </div>

      {/* Flow + pipeline */}
      <div className="flex flex-wrap gap-5">
        <Card className="min-w-0 flex-[1.7_1_600px] px-7 py-7 sm:px-8">
          <div className="flex flex-wrap justify-between gap-3">
            <CardTitle title="Newly at risk and recovered" subtitle={`EGP per day, last ${days} days`} />
            <div className="flex gap-4 text-[13px] text-ink-2">
              {FLOW.map((f) => (
                <span key={f.key} className="inline-flex items-center gap-1.5">
                  <span className="h-0.5 w-3.5" style={{ background: f.color }} aria-hidden />
                  {f.label}
                </span>
              ))}
            </div>
          </div>
          <div className="mt-6">
            <LineChart
              label="Newly at risk and recovered EGP per day"
              format="money"
              series={FLOW}
              data={daily.map((d) => ({
                label: d.label,
                values: { detected: toNumber(d.detected_value), recovered: toNumber(d.recovered_value) },
              }))}
            />
          </div>
        </Card>

        <Card className="min-w-0 flex-[1_1_380px] px-7 py-7 sm:px-8">
          <CardTitle title="Case pipeline" subtitle={`Where the ${count(openCases)} open cases are now`} />
          <div className="mt-5 mb-3.5 flex h-1.5 gap-0.5 overflow-hidden rounded-full" aria-hidden>
            {stages
              .filter((r) => r.cases > 0)
              .map((r) => (
                <span key={r.status} style={{ flex: r.cases, background: caseStatusStyle(r.status).dot }} />
              ))}
          </div>
          <ol>
            {stages.map((r) => {
              const st = caseStatusStyle(r.status);
              return (
                <li key={r.status}>
                  <Link
                    href={`/cases?status=${r.status}`}
                    className="grid grid-cols-[14px_1fr_auto] items-center gap-3 border-t border-line-3 py-2.5 text-sm text-ink-2 hover:text-ink"
                  >
                    <span className="size-[7px] rounded-full" style={{ background: st.dot }} aria-hidden />
                    <span>{CASE_STATUSES[r.status].label}</span>
                    <span className={cn("num", st.tone === "warn" ? "text-warn" : st.tone === "bad" ? "text-bad" : "text-ink")}>
                      {r.cases}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ol>
        </Card>
      </div>

      {/* Lists */}
      <div className="flex flex-wrap gap-5">
        <Card className="min-w-0 flex-[1_1_460px] overflow-hidden">
          <div className="px-6 pt-6 pb-4">
            <CardTitle
              title="Waiting for you"
              subtitle="Needs a person’s approval or hands"
              action={
                <Link href="/approvals" className="text-[13px] text-ink-2 hover:text-ink">
                  Open queue
                </Link>
              }
            />
          </div>
          {waiting.length ? (
            waiting.slice(0, 5).map((e) => (
              <Row
                key={e.id}
                e={e}
                c={cases[e.case_id]}
                right={
                  <>
                    {cases[e.case_id] && <span className="num">{moneyCompact(cases[e.case_id].value_at_risk, "")}</span>}
                    <span className="w-16 text-end text-[12px] text-warn">
                      {e.status === "pending_approval" ? "Approve" : "Your task"}
                    </span>
                  </>
                }
              />
            ))
          ) : (
            <p className="border-t border-line-2 px-6 py-5 text-[13px] text-muted">All clear.</p>
          )}
        </Card>

        <Card className="min-w-0 flex-[1_1_460px] overflow-hidden">
          <div className="px-6 pt-6 pb-4">
            <CardTitle
              title="Recent activity"
              subtitle="The latest actions, automatic or not"
              action={
                <Link href="/activity" className="text-[13px] text-ink-2 hover:text-ink">
                  All activity
                </Link>
              }
            />
          </div>
          {recent.length ? (
            recent.map((e) => <Row key={e.id} e={e} c={cases[e.case_id]} right={<ExecutionStatusText status={e.status} />} />)
          ) : (
            <p className="border-t border-line-2 px-6 py-5 text-[13px] text-muted">No actions yet.</p>
          )}
        </Card>
      </div>
    </>
  );
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: "good" }) {
  return (
    <div>
      <p className="text-[13px] text-muted">{label}</p>
      <p className={cn("serif num mt-1.5 text-[26px]", tone === "good" && "text-good")}>{value}</p>
    </div>
  );
}

function Row({ e, c, right }: { e: Execution; c: CaseDetail | undefined; right: React.ReactNode }) {
  const who = e.decided_by === "auto" ? "Automatic" : e.decided_by ? e.decided_by : null;
  return (
    <Link href={`/cases/${e.case_id}`} className="flex items-center gap-3.5 border-t border-line-2 px-6 py-[15px] hover:bg-hover">
      <span className="min-w-0 flex-1">
        <span className="block truncate">{actionLabel(e.action_type)}</span>
        <span className="mt-0.5 block truncate text-[12px] text-muted">
          {[c && subjectLabel(c.subject_type, c.subject_id), who, relativeTime(e.finished_at ?? e.requested_at)]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
      {right}
    </Link>
  );
}

function sumCaseValues(executions: Execution[], cases: Record<string, CaseDetail>) {
  const ids = new Set(executions.map((e) => e.case_id));
  return [...ids].reduce((a, id) => a + toNumber(cases[id]?.value_at_risk), 0);
}

/** A plain-language summary built from the numbers on this page. No model writes it. */
function Brief({ s, days, newly, waiting }: { s: Stats; days: number; newly: number; waiting: number }) {
  const top = s.by_type[0];
  const topShare = top && toNumber(s.open.value) ? toNumber(top.value) / toNumber(s.open.value) : 0;
  const decided = s.actions.auto + s.actions.approved + s.actions.by_person;
  return (
    <>
      {toNumber(s.recovered.value) > 0 ? (
        <>
          In the last {days} days the agents recovered <span className="text-good">{moneyCompact(s.recovered.value)}</span>{" "}
          across {count(s.recovered.cases)} case{s.recovered.cases === 1 ? "" : "s"}.{" "}
        </>
      ) : (
        <>
          Nothing has been recovered yet in the last {days} days: cases count as recovered once the store shows the problem
          gone after an action.{" "}
        </>
      )}
      {decided > 0 && (
        <>
          Of {count(decided)} action{decided === 1 ? "" : "s"} carried out, {count(s.actions.auto)} ran without anyone’s help.{" "}
        </>
      )}
      {top && (
        <>
          Most of what remains at risk is {CASE_TYPES[top.case_type].label.toLowerCase()} ({percent(topShare)} of{" "}
          {moneyCompact(s.open.value)}).{" "}
        </>
      )}
      {newly > 0 && <>{moneyCompact(newly)} became newly at risk in the period. </>}
      {waiting > 0 && (
        <>
          {count(waiting)} decision{waiting === 1 ? " is" : "s are"} waiting for you.{" "}
        </>
      )}
      {s.actions.failed > 0 && (
        <span className="text-bad">
          {count(s.actions.failed)} action{s.actions.failed === 1 ? "" : "s"} failed.
        </span>
      )}
    </>
  );
}
