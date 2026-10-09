import { ApprovalControls, TaskControls, UndoControl } from "./controls";
import { Bar, Card, CardTitle, ExecutionStatusText, RuleChip, TierText, cn, toneDot } from "./ui";
import {
  SOURCE_LABELS,
  EVENT_LABELS,
  actionLabel,
  dateTime,
  humanize,
  money,
  moneyCompact,
  paramsText,
  percent,
  relativeTime,
  toNumber,
} from "@/lib/format";
import type { CaseAction, CaseDetail, CaseEvent, Execution, InvestigationReport, OutboxMessage } from "@/lib/types";

// Actions whose effect the executor can reverse (store change undone, unsent messages cancelled).
export const UNDOABLE = new Set([
  "send_confirmation_reminder",
  "send_cart_reminder",
  "notify_customer_of_delay",
  "request_deposit",
  "nudge_vendor",
  "open_courier_ticket",
  "hold_dispatch",
  "offer_discount",
]);

// ------------------------------------------------------------ the decision

export function WhyPanel({ c, rules }: { c: CaseDetail; rules: Record<string, string> }) {
  const executionOf = Object.fromEntries(c.executions.map((e) => [e.case_action_id, e]));
  const verb = c.subject_type === "cart" ? "Ordered" : c.subject_type === "return" ? "Kept" : "Delivered";
  return (
    <Card className="flex flex-col gap-6 px-6 py-8 sm:px-9">
      <CardTitle
        title="Why this action"
        subtitle={`Strategist · ${c.actions.length} option${c.actions.length === 1 ? "" : "s"} scored against the store’s policies`}
      />
      {c.plan && <p className="serif text-[19px] leading-[1.55] font-light text-prose sm:text-[21px]">{c.plan.approach}</p>}
      {!c.next_action_id && (
        <p className="rounded-[10px] bg-inset px-4 py-3 text-[13px] text-ink-2">
          {c.executions.some((e) => e.status === "pending_approval" || e.status === "awaiting_human")
            ? "An action is waiting for a person. The next one comes after it."
            : "Nothing is ready to run right now: the remaining options wait for an earlier step to have time to work, or expect no gain."}
        </p>
      )}
      <div>
        {c.actions.map((a) => (
          <Option
            key={a.id}
            a={a}
            next={a.id === c.next_action_id}
            ran={executionOf[a.id]}
            rules={rules}
            verb={verb}
          />
        ))}
      </div>
    </Card>
  );
}

function Option({
  a,
  next,
  ran,
  rules,
  verb,
}: {
  a: CaseAction;
  next: boolean;
  ran: Execution | undefined;
  rules: Record<string, string>;
  verb: string;
}) {
  const ev = toNumber(a.expected_value);
  return (
    <div className="grid grid-cols-[32px_minmax(0,1fr)_auto] items-start gap-[18px] border-t border-line py-[18px]">
      <span className={cn("serif num pt-0.5 text-xl", next ? "text-ink" : "text-faint")}>{a.rank}</span>
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span className="text-[15px] font-medium">{actionLabel(a.action_type)}</span>
          {Object.keys(a.params).length > 0 && <span className="text-[12px] text-muted">{paramsText(a.params)}</span>}
          <TierText tier={a.tier} />
          {next && <span className="text-[12px] font-medium text-ink">· Next</span>}
          {ran && <ExecutionStatusText status={ran.status} />}
        </p>
        <div className="mt-2.5 flex flex-wrap items-center gap-3 text-[12px] text-muted">
          <span className="relative h-[3px] w-[120px] bg-track" aria-hidden>
            <span className="absolute inset-y-0 start-0 bg-good-mark" style={{ width: `${a.p_with * 100}%` }} />
            <span className="absolute inset-y-0 start-0 bg-c6" style={{ width: `${a.p_without * 100}%` }} />
          </span>
          <span>
            {verb} {percent(a.p_with)} with it, {percent(a.p_without)} without · costs {money(a.cost)} ·{" "}
            {a.measured ? (
              <span title="Measured from simulated outcomes. See Simulation.">measured over {a.trials} trials</span>
            ) : (
              <span title="A hand-set starting estimate, until there are enough outcomes.">starting estimate</span>
            )}
          </span>
        </div>
        {!ran && !a.ready && a.waiting_for && <p className="mt-2 text-[12px] text-muted">Next step: {a.waiting_for}</p>}
        {next ? (
          <div className="mt-4 space-y-3 border-s-2 border-line ps-4">
            <p className="text-sm leading-relaxed text-prose">{a.rationale}</p>
            <ul className="space-y-1.5">
              {a.tier_reasons.map((r) => (
                <li key={r.text} className="flex flex-wrap items-baseline gap-2 text-[13px] text-ink-2">
                  <span>{r.text}</span>
                  {r.rule && <RuleChip id={r.rule} text={rules[r.rule]} />}
                </li>
              ))}
            </ul>
            {a.policy_refs.length > 0 && (
              <p className="flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                Cited {a.policy_refs.map((r) => <RuleChip key={r} id={r} text={rules[r]} />)}
              </p>
            )}
          </div>
        ) : (
          <p className="mt-2 text-[13px] leading-relaxed text-muted">{a.rationale}</p>
        )}
      </div>
      <p className="text-end">
        <span className={cn("serif num text-[22px]", ev > 0 ? "text-good" : "text-muted")} title={money(a.expected_value)}>
          {ev > 0 ? "+" : ""}
          {moneyCompact(a.expected_value, "")}
        </span>
        <span className="mt-0.5 block text-[12px] text-muted">expected EGP</span>
      </p>
    </div>
  );
}

// ---------------------------------------------------------- investigation

export function InvestigationPanel({ c }: { c: CaseDetail }) {
  const inv = c.investigation;
  const report: InvestigationReport | undefined = inv?.report;
  if (!inv || !report) return null;
  return (
    <Card className="flex flex-col gap-6 px-6 py-8 sm:px-9">
      <CardTitle
        title="Investigation"
        subtitle={`Investigator · ${inv.turns} turns · ${Math.round(inv.duration_ms / 1000)} s · ${moneyCompact(
          inv.usage.input_tokens + inv.usage.output_tokens,
          "",
        )} tokens · ${inv.model}`}
      />
      <p className="text-[15px] leading-[1.65] text-prose">{report.summary}</p>
      <div className="grid grid-cols-1 items-baseline gap-x-5 gap-y-2.5 border-y border-line py-[22px] sm:grid-cols-[120px_minmax(0,1fr)]">
        <p className="text-[13px] text-muted">Likely cause</p>
        <p className="serif text-[19px] leading-normal">{report.likely_cause}</p>
        <p className="text-[13px] text-muted">Confidence</p>
        <div className="flex items-center gap-3.5">
          <Bar value={report.confidence} width="180px" label="Confidence" />
          <span className="num text-[13px] text-ink-2">{percent(report.confidence)}</span>
        </div>
      </div>
      <div>
        <p className="mb-2.5 font-medium">Evidence</p>
        <ul>
          {report.evidence.map((e) => (
            <li key={e.fact} className="flex gap-4 border-t border-line-3 py-2.5 text-sm leading-normal text-prose">
              <span className="w-[120px] shrink-0 text-[12px] text-muted">{SOURCE_LABELS[e.source] ?? humanize(e.source)}</span>
              <span className="min-w-0">{e.fact}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-6">
        <Factors title="Risk factors" items={report.risk_factors} className="text-bad" />
        <Factors title="In its favour" items={report.mitigating_factors} className="text-good" />
        <Factors title="Couldn’t find out" items={report.missing_information} className="text-muted" />
      </div>
    </Card>
  );
}

function Factors({ title, items, className }: { title: string; items: string[]; className: string }) {
  if (!items.length) return null;
  return (
    <div>
      <p className={cn("mb-2 text-[13px]", className)}>{title}</p>
      <ul className="space-y-1.5 text-sm leading-normal text-ink-2">
        {items.map((i) => (
          <li key={i}>{i}</li>
        ))}
      </ul>
    </div>
  );
}

// -------------------------------------------------------- actions taken

export function ExecutionItem({ e, rules }: { e: Execution; rules: Record<string, string> }) {
  const changes = diff(e.before, e.after);
  const meta = [
    Object.keys(e.params).length ? paramsText(e.params) : null,
    decided(e),
    e.status === "succeeded" && UNDOABLE.has(e.action_type) ? "can be undone" : null,
  ].filter(Boolean);
  return (
    <div className="border-t border-line py-4">
      <div className="flex justify-between gap-2.5">
        <span className="font-medium">{actionLabel(e.action_type)}</span>
        <ExecutionStatusText status={e.status} />
      </div>
      <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[13px] text-muted">
        {meta.join(" · ")}
        {e.policy_refs.slice(0, 3).map((r) => (
          <RuleChip key={r} id={r} text={rules[r]} />
        ))}
      </p>
      {e.error && <p className="mt-2 text-[13px] text-bad">{e.error}</p>}
      {e.decision_note && <p className="mt-2 text-[13px] text-ink-2">“{e.decision_note}”</p>}
      {typeof e.result?.outcome === "string" && <p className="mt-2 text-[13px] text-ink-2">Outcome: {e.result.outcome}</p>}
      {typeof e.result?.code === "string" && (
        <p className="mt-2 text-[13px] text-ink-2">
          Code <span className="font-mono text-ink">{e.result.code}</span>
        </p>
      )}
      {changes.map(([k, before, after]) => (
        <p key={k} className="mt-2.5 font-mono text-[12px] text-muted">
          {humanize(k).toLowerCase()}: {before} <span className="text-faint">→</span> <span className="text-ink">{after}</span>
        </p>
      ))}
      {e.messages.map((m) => (
        <MessageBody key={m.id} m={m} className="mt-3 text-[14px]" />
      ))}
      {e.rolled_back_by && (
        <p className="mt-2 text-[12px] text-muted">
          Undone by {e.rolled_back_by} {relativeTime(e.rolled_back_at)}
          {typeof e.rollback_result?.reason === "string" && `: “${e.rollback_result.reason}”`}
        </p>
      )}
      <div className="mt-3.5 empty:hidden">
        {e.status === "pending_approval" && <ApprovalControls executionId={e.id} />}
        {e.status === "awaiting_human" && <TaskControls executionId={e.id} />}
        {e.status === "succeeded" && UNDOABLE.has(e.action_type) && <UndoControl executionId={e.id} />}
      </div>
    </div>
  );
}

export function decided(e: Execution): string {
  if (e.status === "pending_approval") return `requested ${relativeTime(e.requested_at)}`;
  if (e.status === "awaiting_human") return `a person’s task since ${relativeTime(e.requested_at)}`;
  if (e.decided_by === "auto") return `Automatic · ${relativeTime(e.finished_at)}`;
  if (e.status === "rejected") return `Rejected by ${e.decided_by} · ${relativeTime(e.decided_at)}`;
  if (e.decided_by) return `${e.tier === "human_only" ? "Done" : "Approved"} by ${e.decided_by} · ${relativeTime(e.decided_at)}`;
  return relativeTime(e.requested_at);
}

export function diff(before: Record<string, unknown> | null, after: Record<string, unknown> | null) {
  if (!before || !after) return [];
  const show = (v: unknown) => (v === undefined || v === null ? "—" : typeof v === "boolean" ? (v ? "yes" : "no") : String(v));
  return Object.keys(after)
    .filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
    .map((k) => [k, show(before[k]), show(after[k])] as const);
}

export function MessageBody({ m, className }: { m: OutboxMessage; className?: string }) {
  const arabic = m.language === "ar";
  return (
    <div
      dir={arabic ? "rtl" : "auto"}
      lang={m.language}
      className={cn(
        "rounded-xl bg-inset px-5 py-[18px] leading-[1.8] whitespace-pre-line text-prose",
        arabic && "arabic",
        m.status === "cancelled" && "text-muted line-through decoration-faint",
        className,
      )}
    >
      {m.body}
    </div>
  );
}

// -------------------------------------------------------------- audit trail

export function AuditTrail({ events }: { events: CaseEvent[] }) {
  return (
    <ol>
      {[...events].reverse().map((ev, i, all) => (
        <li key={`${ev.at}-${i}`} className="grid grid-cols-[12px_1fr] gap-3.5 pb-4">
          <span className="flex flex-col items-center">
            <span className="mt-1.5 size-[7px] rounded-full" style={{ background: eventDot(ev) }} aria-hidden />
            {i < all.length - 1 && <span className="mt-1.5 w-px flex-1 bg-line" aria-hidden />}
          </span>
          <div className="min-w-0">
            <p>
              {EVENT_LABELS[ev.type] ?? humanize(ev.type)}
              {typeof ev.data.action === "string" && ` · ${actionLabel(ev.data.action)}`}
            </p>
            <p className="mt-[3px] text-[12px] text-muted">
              {actorLabel(ev.actor)} · {dateTime(ev.at)}
            </p>
            {typeof ev.data.reason === "string" && <p className="mt-1 text-[12px] text-ink-2">{ev.data.reason}</p>}
            {typeof ev.data.note === "string" && <p className="mt-1 text-[12px] text-ink-2">“{ev.data.note}”</p>}
            {typeof ev.data.error === "string" && <p className="mt-1 text-[12px] text-bad">{ev.data.error}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function eventDot(ev: CaseEvent): string {
  if (ev.type.endsWith("failed")) return toneDot.bad;
  if (ev.type === "action_requested" || ev.type === "action_approved") return toneDot.warn;
  if (ev.type === "action_succeeded" || ev.type === "action_completed") return toneDot.good;
  return "var(--c6)";
}

const ACTORS: Record<string, string> = {
  detector: "Detector",
  investigator: "Investigator",
  strategist: "Strategist",
  executor: "Executor",
  auto: "Automatic",
  system: "System",
};
const actorLabel = (a: string) => ACTORS[a] ?? a;

