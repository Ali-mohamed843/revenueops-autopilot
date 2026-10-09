import type { Metadata } from "next";
import Link from "next/link";

import { ButtonLink, Card, CaseStatusText, EmptyState, PageTitle, cn } from "@/components/ui";
import { api } from "@/lib/api";
import { CASE_TYPES, count, money, relativeTime, subjectLabel, toNumber } from "@/lib/format";
import type { CaseStatus, CaseType } from "@/lib/types";

export const metadata: Metadata = { title: "Cases" };

const PAGE = 50;

const STAGES: { key: string; label: string; statuses: CaseStatus[] }[] = [
  {
    key: "open",
    label: "All open",
    statuses: ["open", "investigation_failed", "investigated", "planning_failed", "planned", "acting", "acted"],
  },
  { key: "new", label: "New", statuses: ["open", "investigation_failed"] },
  { key: "investigated", label: "Investigated", statuses: ["investigated", "planning_failed"] },
  { key: "planned", label: "Planned", statuses: ["planned"] },
  { key: "acting", label: "Waiting for a person", statuses: ["acting"] },
  { key: "acted", label: "Action taken", statuses: ["acted"] },
  { key: "closed", label: "Closed", statuses: ["closed"] },
];

const STATUS_TO_STAGE: Partial<Record<string, string>> = {
  open: "new",
  investigation_failed: "new",
  investigated: "investigated",
  planning_failed: "investigated",
  planned: "planned",
  acting: "acting",
  acted: "acted",
  closed: "closed",
};

export default async function CasesPage({
  searchParams,
}: {
  searchParams: Promise<{ stage?: string; status?: string; type?: string; q?: string; show?: string }>;
}) {
  const params = await searchParams;
  const stageKey = params.stage ?? (params.status ? STATUS_TO_STAGE[params.status] : undefined) ?? "open";
  const stage = STAGES.find((s) => s.key === stageKey) ?? STAGES[0];
  const type = params.type && params.type in CASE_TYPES ? (params.type as CaseType) : undefined;
  const q = (params.q ?? "").trim().toLowerCase();
  const shown = Math.max(PAGE, Number(params.show) || PAGE);

  const [all, stats] = await Promise.all([
    api.cases({ status: stage.statuses, case_type: type ? [type] : undefined }),
    api.stats(30),
  ]);
  const cases = q
    ? all.filter((c) =>
        [c.title, c.subject_id, c.subject_type, CASE_TYPES[c.case_type].label].some((f) => f.toLowerCase().includes(q)),
      )
    : all;
  const total = cases.reduce((sum, c) => sum + toNumber(c.value_at_risk), 0);
  const stageCount = Object.fromEntries(stats.by_status.map((r) => [r.status, r.cases]));
  const countFor = (s: (typeof STAGES)[number]) =>
    s.key === "closed" ? undefined : s.statuses.reduce((a, st) => a + (stageCount[st] ?? 0), 0);

  const href = (next: { stage?: string; type?: string | null; show?: number }) => {
    const u = new URLSearchParams();
    const s = next.stage ?? stage.key;
    const t = next.type === undefined ? type : next.type;
    if (s !== "open") u.set("stage", s);
    if (t) u.set("type", t);
    if (q) u.set("q", q);
    if (next.show) u.set("show", String(next.show));
    const qs = u.toString();
    return qs ? `/cases?${qs}` : "/cases";
  };

  return (
    <>
      <PageTitle
        title="Cases"
        subtitle="Every situation where revenue is at risk, most urgent first. Open one to see why the agents chose what they did."
        side={
          <form action="/cases" className="w-full flex-[0_1_340px]" role="search">
            {stage.key !== "open" && <input type="hidden" name="stage" value={stage.key} />}
            {type && <input type="hidden" name="type" value={type} />}
            <label>
              <span className="sr-only">Search cases</span>
              <input
                type="search"
                name="q"
                defaultValue={params.q ?? ""}
                placeholder="Search order, cart or title"
                className="h-10 w-full rounded-[10px] border border-field bg-card px-3.5 text-sm text-ink placeholder:text-faint"
              />
            </label>
          </form>
        }
      />

      <div className="mt-2 flex flex-col gap-[18px]">
        <nav aria-label="Stage" className="flex gap-7 overflow-x-auto border-b border-line-2">
          {STAGES.map((s) => {
            const n = countFor(s);
            const active = s.key === stage.key;
            return (
              <Link
                key={s.key}
                href={href({ stage: s.key })}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-11 items-center gap-2 border-b-2 text-sm whitespace-nowrap",
                  active ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink",
                )}
              >
                {s.label}
                {n !== undefined && <span className={cn("num", s.key === "acting" && n > 0 ? "text-warn" : "text-muted")}>{n}</span>}
              </Link>
            );
          })}
        </nav>
        <div aria-label="Case type" className="flex flex-wrap gap-2">
          <Chip href={href({ type: null })} active={!type}>
            All types
          </Chip>
          {(Object.keys(CASE_TYPES) as CaseType[]).map((t) => (
            <Chip key={t} href={href({ type: t })} active={type === t}>
              {CASE_TYPES[t].label}
            </Chip>
          ))}
        </div>
      </div>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-baseline justify-between gap-3 px-7 py-[22px]">
          <p className="text-[13px] text-muted">
            {count(cases.length)} case{cases.length === 1 ? "" : "s"}
            {q && <> matching “{params.q}”</>}
            {all.length === 200 && " (the 200 most urgent)"}
          </p>
          <p className="serif num text-2xl font-light">
            {money(total, "")} <span className="font-sans text-[13px] text-muted">EGP at risk</span>
          </p>
        </div>
        {cases.length === 0 ? (
          <EmptyState title="No cases here">Try another stage, type or search, or scan the store from the Overview.</EmptyState>
        ) : (
          <>
            <div
              aria-hidden
              className="hidden grid-cols-[minmax(0,1fr)_130px_150px_180px_110px] gap-5 border-t border-line-2 px-7 py-2.5 text-[12px] text-muted md:grid"
            >
              <span>Case</span>
              <span>Subject</span>
              <span className="text-end">At risk</span>
              <span>Stage</span>
              <span>Detected</span>
            </div>
            <ul>
              {cases.slice(0, shown).map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/cases/${c.id}`}
                    className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-5 gap-y-1 border-t border-line-2 px-7 py-[17px] hover:bg-hover md:grid-cols-[minmax(0,1fr)_130px_150px_180px_110px]"
                  >
                    <span className="min-w-0">
                      <span className="block truncate">{c.title}</span>
                      <span className="mt-[3px] block text-[12px] text-muted">{CASE_TYPES[c.case_type].label}</span>
                    </span>
                    <span className="hidden font-mono text-[12px] text-muted md:block">{subjectLabel(c.subject_type, c.subject_id)}</span>
                    <span className="serif num text-end text-[19px]">{money(c.value_at_risk, "")}</span>
                    <span className="hidden md:block">
                      <CaseStatusText status={c.status} />
                    </span>
                    <span className="hidden text-[13px] text-muted md:block">{relativeTime(c.detected_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
            {cases.length > shown && (
              <div className="flex justify-center border-t border-line-2 p-[18px]">
                <ButtonLink href={href({ show: shown + PAGE })} scroll={false}>
                  Show {Math.min(PAGE, cases.length - shown)} more
                </ButtonLink>
              </div>
            )}
          </>
        )}
      </Card>
    </>
  );
}

function Chip({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      className={cn(
        "inline-flex h-8 items-center rounded-lg px-3 text-[13px] transition-colors",
        active ? "bg-primary text-primary-ink" : "text-ink-2 shadow-[inset_0_0_0_1px_var(--field)] hover:text-ink",
      )}
    >
      {children}
    </Link>
  );
}
