import type { Metadata } from "next";
import Link from "next/link";

import { UNDOABLE, decided, diff } from "@/components/case-parts";
import { UndoControl } from "@/components/controls";
import { Card, EmptyState, PageTitle, Segmented, StatusText, executionTone } from "@/components/ui";
import { api } from "@/lib/api";
import { casesById } from "@/lib/cases";
import { EXECUTION_STATUSES, actionLabel, cairoDay, clock, dayHeading, humanize, money, paramsText, subjectLabel } from "@/lib/format";
import type { Execution, ExecutionStatus } from "@/lib/types";

export const metadata: Metadata = { title: "Activity" };

const FILTERS: { key: string; label: string; statuses?: ExecutionStatus[]; tone?: "warn" | "bad" }[] = [
  { key: "all", label: "All" },
  { key: "done", label: "Done", statuses: ["succeeded"] },
  { key: "waiting", label: "Waiting", statuses: ["pending_approval", "awaiting_human"], tone: "warn" },
  { key: "undone", label: "Undone", statuses: ["rolled_back"] },
  { key: "problems", label: "Rejected or failed", statuses: ["rejected", "failed"], tone: "bad" },
];

export default async function ActivityPage({ searchParams }: { searchParams: Promise<{ show?: string }> }) {
  const show = (await searchParams).show ?? "all";
  const filter = FILTERS.find((f) => f.key === show) ?? FILTERS[0];
  const all = await api.executions({ limit: 200 });
  const executions = filter.statuses ? all.filter((e) => filter.statuses!.includes(e.status)) : all;
  const cases = await casesById(executions.map((e) => e.case_id));

  const days = new Map<string, Execution[]>();
  for (const e of executions) {
    const day = cairoDay(e.finished_at ?? e.requested_at);
    days.set(day, [...(days.get(day) ?? []), e]);
  }

  return (
    <>
      <PageTitle
        title="Activity"
        subtitle="Every action the service took or requested: who decided, what changed in the store, and the messages it drafted. Undo what can be undone."
        side={
          <Segmented
            label="Filter"
            items={FILTERS.map((f) => {
              const n = f.statuses ? all.filter((e) => f.statuses!.includes(e.status)).length : undefined;
              return {
                href: f.key === "all" ? "/activity" : `/activity?show=${f.key}`,
                label: f.label,
                active: f.key === filter.key,
                count: f.tone && n ? n : undefined,
                countTone: f.tone,
              };
            })}
          />
        }
      />

      {executions.length === 0 ? (
        <Card className="mt-3">
          <EmptyState title="No actions here yet" />
        </Card>
      ) : (
        [...days.entries()].map(([day, list]) => (
          <Card key={day} aria-label={dayHeading(day)} className="mt-3 overflow-hidden">
            <h2 className="px-7 py-5 text-[13px] font-medium text-muted">{dayHeading(day)}</h2>
            <ol>
              {list.map((e) => {
                const c = cases[e.case_id];
                const tone = executionTone(e.status);
                const change = diff(e.before, e.after)
                  .map(([k, b, a]) => `${humanize(k).toLowerCase()}: ${b} → ${a}`)
                  .join(", ");
                return (
                  <li
                    key={e.id}
                    className="grid grid-cols-[56px_minmax(0,1fr)] gap-5 border-t border-line-2 px-7 py-[22px] sm:grid-cols-[72px_minmax(0,1fr)]"
                    style={{
                      background:
                        tone === "warn"
                          ? "color-mix(in srgb, var(--warn-mark) 4%, transparent)"
                          : tone === "bad"
                            ? "var(--bad-soft)"
                            : undefined,
                    }}
                  >
                    <span className="num pt-[3px] text-[13px] text-muted">{clock(e.finished_at ?? e.requested_at)}</span>
                    <div className="flex flex-wrap items-center gap-x-7 gap-y-3">
                      <div className="min-w-0 flex-[1_1_380px]">
                        <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                          <span className="text-[15px] font-medium">{actionLabel(e.action_type)}</span>
                          <StatusText tone={tone} className="text-[12px]">
                            {EXECUTION_STATUSES[e.status]}
                          </StatusText>
                        </p>
                        <p className="mt-1.5 text-sm leading-normal text-ink-2">{detail(e)}</p>
                        <p className="mt-2 text-[12px] text-muted">
                          {decided(e)}
                          {c && (
                            <>
                              {" · "}
                              <Link href={`/cases/${c.id}`} className="text-ink-2 hover:text-ink">
                                {subjectLabel(c.subject_type, c.subject_id)}
                              </Link>
                            </>
                          )}
                        </p>
                      </div>
                      {change && <span className="font-mono text-[12px] text-muted">{change}</span>}
                      {c && (
                        <p className="min-w-[110px] text-end">
                          <span className="serif num text-xl">{money(c.value_at_risk, "").split(".")[0]}</span>{" "}
                          <span className="text-[12px] text-muted">EGP</span>
                        </p>
                      )}
                      {e.status === "succeeded" && UNDOABLE.has(e.action_type) && <UndoControl executionId={e.id} />}
                    </div>
                  </li>
                );
              })}
            </ol>
          </Card>
        ))
      )}
    </>
  );
}

/** One plain line about what the action did or why it's waiting. */
function detail(e: Execution): string {
  if (e.error) return e.error;
  const parts: string[] = [];
  const m = e.messages[0];
  // A message names its own channel below, so leave the channel out of the parameters.
  const params = Object.fromEntries(Object.entries(e.params).filter(([k]) => !(m && k === "channel")));
  if (Object.keys(params).length) parts.push(paramsText(params));
  if (m) {
    const channel = { whatsapp: "WhatsApp", sms: "SMS", vendor: "Message to the vendor", courier: "Courier ticket" }[m.channel] ?? m.channel;
    parts.push(`${channel}${m.language === "ar" ? " in Arabic" : ""}, ${m.status === "cancelled" ? "cancelled" : "queued in the outbox"}`);
  }
  if (typeof e.result?.code === "string") parts.push(`code ${e.result.code}`);
  if (typeof e.result?.outcome === "string") parts.push(e.result.outcome);
  if (e.decision_note) parts.push(`“${e.decision_note}”`);
  if (typeof e.rollback_result?.reason === "string") parts.push(`Undone: ${e.rollback_result.reason}`);
  if (!parts.length && (e.status === "pending_approval" || e.status === "awaiting_human")) parts.push("Waiting for a person");
  return parts.join(" · ");
}
