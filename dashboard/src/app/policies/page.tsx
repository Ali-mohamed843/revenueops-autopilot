import type { Metadata } from "next";
import { Lock } from "lucide-react";

import { PageTitle } from "@/components/ui";
import { api } from "@/lib/api";
import { CASE_TYPES } from "@/lib/format";

export const metadata: Metadata = { title: "Policies" };

// Rules the decision engine also enforces in code (decision/score.py, decision/escalation.py).
const IN_CODE = new Set(["RET-2", "DISC-1", "DISC-2", "DISC-3", "COD-3", "COD-4", "COD-6", "FUL-2", "DISC-5"]);

const list = (items: string[]) =>
  items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

export default async function PoliciesPage() {
  const policies = await api.policies();
  const ruleCount = policies.reduce((a, p) => a + p.rules.length, 0);
  const hard = policies.reduce((a, p) => a + p.rules.filter((r) => IN_CODE.has(r.id)).length, 0);

  return (
    <>
      <div className="max-w-[720px]">
        <PageTitle
          title="Policies"
          subtitle={
            <>
              The store’s rules. The Strategist must cite them, and each case only sees the policies tagged with its type.
              Rules marked <HardLimit /> are also enforced in code, so the model can’t override them.
            </>
          }
        />
      </div>

      <div className="mt-4 flex flex-wrap items-start gap-12">
        <nav aria-label="Policies" className="flex flex-[1_1_220px] flex-col lg:sticky lg:top-6">
          {policies.map((p) => (
            <a
              key={p.id}
              href={`#${p.id}`}
              className="flex items-baseline justify-between gap-2.5 border-t border-line-2 py-[11px] text-ink-2 hover:text-ink"
            >
              <span>{p.title}</span>
              <span className="font-mono text-[12px] text-muted">{p.id}</span>
            </a>
          ))}
          <div className="border-t border-line-2 pt-5">
            <p className="text-[13px] text-muted">Hard limits</p>
            <p className="mt-1">
              <span className="serif num text-[32px] font-light">{hard}</span>{" "}
              <span className="text-[13px] text-muted">of {ruleCount} rules</span>
            </p>
          </div>
        </nav>

        <div className="flex min-w-0 flex-[999_1_640px] flex-col gap-5">
          {policies.map((p) => (
            <section key={p.id} id={p.id} className="card scroll-mt-6 px-6 pt-[30px] pb-4 sm:px-[34px]">
              <p className="font-mono text-[12px] text-muted">{p.id}</p>
              <h2 className="serif mt-1.5 text-[28px] font-normal">{p.title}</h2>
              <p className="mt-2 mb-[18px] text-[13px] text-muted">
                Applies to {list(p.applies_to.map((t) => CASE_TYPES[t]?.label.toLowerCase() ?? t))}
              </p>
              <ol>
                {p.rules.map((r) => (
                  <li
                    key={r.id}
                    id={r.id}
                    className="grid scroll-mt-24 grid-cols-[64px_minmax(0,1fr)] items-baseline gap-x-4 gap-y-1 border-t border-line-2 py-3.5 target:bg-[color-mix(in_srgb,var(--warn-mark)_8%,transparent)] sm:grid-cols-[72px_minmax(0,1fr)_auto]"
                  >
                    <span className="font-mono text-[12px] text-muted">{r.id}</span>
                    <span className="text-[15px] leading-relaxed text-prose">{r.text}</span>
                    {IN_CODE.has(r.id) && (
                      <span className="col-start-2 sm:col-start-auto">
                        <HardLimit />
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>
      </div>
    </>
  );
}

function HardLimit() {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] whitespace-nowrap text-good" title="Also enforced by the decision engine">
      <Lock className="size-[11px]" aria-hidden />
      Hard limit
    </span>
  );
}
