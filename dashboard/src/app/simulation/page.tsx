import type { Metadata } from "next";
import Link from "next/link";

import { DryRunButton, OutcomeControls } from "@/components/controls";
import { Card, CardTitle, EmptyState, PageTitle, RuleChip, TierText } from "@/components/ui";
import { api } from "@/lib/api";
import { CASE_TYPES, actionLabel, count, dateTime, moneyCompact, percent, toNumber } from "@/lib/format";
import type { ComparisonRow, DryRunReport } from "@/lib/types";

export const metadata: Metadata = { title: "Simulation" };

const TIER_ROWS: { key: keyof DryRunReport["tiers"]; label: string; tone?: string }[] = [
  { key: "auto", label: "Would run on its own" },
  { key: "approval", label: "Would need approval", tone: "text-warn" },
  { key: "human_only", label: "Would need a person", tone: "text-warn" },
  { key: "waiting", label: "Already waiting for a person" },
  { key: "nothing_ready", label: "Nothing ready yet" },
];

export default async function SimulationPage() {
  const sim = await api.simulation();
  const dry = sim.dry_run;
  const outcomes = sim.outcomes;
  const calibratedFromLatest =
    !!sim.calibration && !!outcomes && sim.calibration.params.outcomes_run === outcomes.id;

  return (
    <>
      <PageTitle
        title="Simulation"
        subtitle="Try the pipeline without touching the store, and measure how well each action works instead of assuming it."
      />

      <p className="mt-2 border-y border-line-2 py-3.5 text-[13px] text-muted">
        Outcomes here are <span className="text-ink">simulated</span> by a seeded model of customer behaviour, not real
        customers. They stand in until there are enough real outcomes to measure. Scoring now uses:{" "}
        <span className="text-ink">{sim.rates_in_use}</span>.
      </p>

      {/* Dry run */}
      <Card className="flex flex-col gap-6 px-6 py-8 sm:px-9">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <CardTitle
            title="Dry run"
            subtitle={
              dry
                ? `${dateTime(dry.created_at)} · scored with ${String(dry.params.rates)} · nothing was executed`
                : "What the pipeline would do right now, without doing any of it"
            }
          />
          <DryRunButton />
        </div>
        {dry ? <DryRun r={dry.report} /> : <EmptyState title="No dry run yet">Run one to see what would happen now.</EmptyState>}
      </Card>

      {/* Outcomes and calibration */}
      <Card className="flex flex-col gap-6 px-6 py-8 sm:px-9">
        <CardTitle
          title="Measured against assumed"
          subtitle={
            outcomes
              ? `${count(outcomes.report.episodes)} simulated outcomes · seed ${outcomes.seed} · ${dateTime(outcomes.created_at)}${
                  calibratedFromLatest ? " · in use for scoring" : ""
                }`
              : "Chance the revenue comes back, with each action and with none"
          }
        />
        <p className="max-w-3xl text-sm leading-relaxed text-ink-2">
          Each simulated case gets a random action from its catalogue, or none (the control group), so the difference
          between the two is a fair measure of what the action adds. A rate is used for scoring once it rests on at
          least 30 trials; until then the starting estimate stays.
        </p>
        <OutcomeControls hasOutcomes={!!outcomes} calibrated={calibratedFromLatest} />
        {outcomes ? (
          <Comparison rows={outcomes.report.comparison} />
        ) : (
          <EmptyState title="No outcomes yet">Simulate outcomes to measure the actions.</EmptyState>
        )}
      </Card>
    </>
  );
}

function DryRun({ r }: { r: DryRunReport }) {
  const acting = r.tiers.auto + r.tiers.approval + r.tiers.human_only;
  const maxAction = Math.max(...Object.values(r.actions), 1);
  return (
    <div className="flex flex-col gap-7">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-5 border-y border-line py-6">
        <Figure label="Cases found" value={count(r.cases)} sub={`${moneyCompact(r.value_at_risk)} at risk`} />
        <Figure label="Expected recovery" value={moneyCompact(r.expected_recovery, "")} sub="EGP, if the next actions run" tone="good" />
        <Figure label="Need a person" value={count(r.approvals_needed)} sub="approvals and tasks" tone={r.approvals_needed ? "warn" : undefined} />
        <Figure
          label="Scored on measured rates"
          value={r.measured_share == null ? "—" : percent(r.measured_share)}
          sub={`of ${count(acting)} next actions`}
        />
      </div>

      <div className="grid gap-8 lg:grid-cols-2">
        <div>
          <p className="mb-2 font-medium">Next action, by tier</p>
          <ul>
            {TIER_ROWS.map((t) => (
              <li key={t.key} className="flex items-baseline justify-between gap-3 border-t border-line-3 py-2.5 text-sm">
                <span className={t.tone ?? "text-ink-2"}>{t.label}</span>
                <span className="num">
                  {count(r.tiers[t.key])}
                  {r.tier_value[t.key] && toNumber(r.tier_value[t.key]) > 0 && (
                    <span className="ms-2 text-[12px] text-muted">{moneyCompact(r.tier_value[t.key])}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[12px] text-muted">
            {count(r.planned_by.plan ?? 0)} cases use their Strategist plan; {count(r.planned_by.catalogue ?? 0)} use every
            catalogue action that fits (no model is called in a dry run).
          </p>
        </div>
        <div>
          <p className="mb-2 font-medium">Action mix</p>
          <ul>
            {Object.entries(r.actions).map(([action, n]) => (
              <li key={action} className="grid grid-cols-[minmax(0,12rem)_1fr_auto] items-center gap-3 border-t border-line-3 py-2.5 text-sm">
                <span className="truncate text-ink-2">{actionLabel(action)}</span>
                <span className="h-[3px] bg-track">
                  <span className="block h-full bg-c2" style={{ width: `${(n / maxAction) * 100}%` }} />
                </span>
                <span className="num">{count(n)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div>
        <p className="mb-2 font-medium">
          High-risk actions <span className="num text-[13px] font-normal text-muted">{count(r.high_risk_total)}</span>
        </p>
        <p className="mb-3 text-[13px] text-muted">Next actions that need a person, move money, or can’t be undone.</p>
        {r.high_risk.length ? (
          <ul>
            {r.high_risk.map((h, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-t border-line-3 py-3 text-sm">
                <span className="min-w-0 flex-[1_1_260px]">
                  <span className="font-medium">{actionLabel(h.action)}</span>{" "}
                  <TierText tier={h.tier} />
                  <span className="mt-0.5 block text-[13px] text-muted">
                    {h.case_id ? (
                      <Link href={`/cases/${h.case_id}`} className="text-ink-2 hover:text-ink">
                        {CASE_TYPES[h.case_type].label} · {h.subject}
                      </Link>
                    ) : (
                      <>
                        {CASE_TYPES[h.case_type].label} · {h.subject} (not opened yet)
                      </>
                    )}
                  </span>
                </span>
                <span className="flex-[1_1_260px] text-[13px] text-ink-2">
                  {h.reason} {h.rule && <RuleChip id={h.rule} />}
                </span>
                <span className="serif num text-lg">{moneyCompact(h.value, "")}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-muted">None.</p>
        )}
      </div>
    </div>
  );
}

function Figure({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: "good" | "warn" }) {
  return (
    <div>
      <p className="text-[13px] text-muted">{label}</p>
      <p className={`serif num mt-1.5 text-[30px] font-light ${tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : ""}`}>
        {value}
      </p>
      <p className="mt-1 text-[12px] text-muted">{sub}</p>
    </div>
  );
}

function Comparison({ rows }: { rows: ComparisonRow[] }) {
  const types = [...new Set(rows.map((r) => r.case_type))];
  return (
    <div className="flex flex-col gap-7">
      <p className="flex flex-wrap items-center gap-5 text-[12px] text-muted">
        <span className="inline-flex items-center gap-2">
          <span className="h-2 w-6 rounded-sm bg-c2/40" aria-hidden /> measured, 95% interval
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="size-2 rounded-full bg-good-mark" aria-hidden /> measured
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="h-3 w-0.5 bg-ink" aria-hidden /> assumed
        </span>
      </p>
      {types.map((t) => (
        <section key={t}>
          <h3 className="serif mb-1 text-xl">{CASE_TYPES[t].label}</h3>
          <ul>
            {rows
              .filter((r) => r.case_type === t)
              .map((r) => (
                <li
                  key={r.action}
                  className="grid grid-cols-1 items-center gap-x-5 gap-y-1.5 border-t border-line-3 py-3 text-sm md:grid-cols-[minmax(0,14rem)_1fr_19rem]"
                >
                  <span className={r.action === "none" ? "text-muted" : "text-ink"}>
                    {r.action === "none" ? "No action (control group)" : actionLabel(r.action)}
                  </span>
                  <span className="relative h-4" aria-hidden>
                    <span className="absolute inset-x-0 top-1/2 h-px bg-line" />
                    {r.measured !== null && (
                      <>
                        <span
                          className="absolute top-1/2 h-2 -translate-y-1/2 rounded-sm bg-c2/40"
                          style={{ left: `${r.low * 100}%`, width: `${Math.max((r.high - r.low) * 100, 0.5)}%` }}
                        />
                        <span
                          className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-good-mark"
                          style={{ left: `${r.measured * 100}%` }}
                        />
                      </>
                    )}
                    <span
                      className="absolute top-0 h-4 w-0.5 -translate-x-1/2 bg-ink"
                      style={{ left: `${r.assumed * 100}%` }}
                    />
                  </span>
                  <span className="num text-[13px] text-ink-2">
                    assumed {percent(r.assumed)} ·{" "}
                    {r.measured === null ? (
                      <span className="text-muted">not measured</span>
                    ) : (
                      <>
                        measured <span className="text-ink">{percent(r.measured)}</span>{" "}
                        <span className="text-muted">
                          ({percent(r.low)}–{percent(r.high)}, n {count(r.trials)})
                        </span>
                        {!r.enough && <span className="text-warn"> · too few</span>}
                      </>
                    )}
                  </span>
                </li>
              ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
