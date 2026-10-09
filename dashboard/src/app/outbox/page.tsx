import type { Metadata } from "next";
import Link from "next/link";

import { MessageBody, UNDOABLE } from "@/components/case-parts";
import { UndoControl } from "@/components/controls";
import { Card, EmptyState, PageTitle, Segmented, StatusText } from "@/components/ui";
import { api } from "@/lib/api";
import { casesById } from "@/lib/cases";
import { actionLabel, clock, maskPhone, subjectLabel } from "@/lib/format";
import type { OutboxMessage } from "@/lib/types";

export const metadata: Metadata = { title: "Outbox" };

const CHANNELS: Record<string, string> = { whatsapp: "WhatsApp", sms: "SMS", vendor: "Vendor message", courier: "Courier ticket" };

// Cancelling a message undoes the action that drafted it; say so when that does more than cancel.
const ALSO_UNDOES: Record<string, string> = {
  offer_discount: "Cancelling also voids the discount code.",
  hold_dispatch: "Cancelling also releases the dispatch hold.",
};

export default async function OutboxPage({ searchParams }: { searchParams: Promise<{ show?: string }> }) {
  const raw = (await searchParams).show;
  const show = raw === "cancelled" || raw === "all" ? raw : "queued";
  const [all, executions] = await Promise.all([api.outbox({ limit: 200 }), api.executions({ limit: 200 })]);
  const messages = show === "all" ? all : all.filter((m) => m.status === show);
  const executionOf = Object.fromEntries(executions.map((e) => [e.id, e]));
  const cases = await casesById(messages.map((m) => m.case_id));
  const queued = all.filter((m) => m.status === "queued").length;

  return (
    <>
      <PageTitle
        title="Outbox"
        subtitle="Messages the agents drafted for customers, vendors and couriers."
        side={
          <Segmented
            label="Filter"
            items={[
              { href: "/outbox", label: "Queued", active: show === "queued", count: queued },
              { href: "/outbox?show=cancelled", label: "Cancelled", active: show === "cancelled" },
              { href: "/outbox?show=all", label: "All", active: show === "all" },
            ]}
          />
        }
      />

      <p className="mt-2 border-y border-line-2 py-3.5 text-[13px] text-muted">
        This version stops at the outbox. Nothing is actually sent.
      </p>

      {messages.length === 0 ? (
        <Card>
          <EmptyState title="No messages here" />
        </Card>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,380px),1fr))] gap-5">
          {messages.map((m) => {
            const e = executionOf[m.execution_id];
            const c = cases[m.case_id];
            const canCancel = m.status === "queued" && e?.status === "succeeded" && UNDOABLE.has(e.action_type);
            return (
              <article key={m.id} className="card flex flex-col gap-[18px] p-6">
                <div className="flex items-baseline justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{recipient(m)}</p>
                    <p className="mt-[3px] text-[12px] text-muted">
                      {CHANNELS[m.channel] ?? m.channel} · {m.language === "ar" ? "Arabic" : "English"} · {clock(m.created_at)} ·
                      drafted by {m.drafted_by}
                    </p>
                  </div>
                  <StatusText tone={m.status === "queued" ? "good" : "muted"} className="text-[12px]">
                    {m.status === "queued" ? "Queued" : "Cancelled"}
                  </StatusText>
                </div>
                <MessageBody m={m} className="text-[15px]" />
                <div className="mt-auto flex flex-wrap items-center justify-between gap-3">
                  <span className="text-[12px] text-muted">
                    {e ? actionLabel(e.action_type) : "Message"}
                    {c && (
                      <>
                        {" · "}
                        <Link href={`/cases/${c.id}`} className="text-ink-2 hover:text-ink">
                          {subjectLabel(c.subject_type, c.subject_id)}
                        </Link>
                      </>
                    )}
                  </span>
                  {canCancel && e && (
                    <UndoControl
                      executionId={e.id}
                      label="Cancel"
                      hint={`Cancelling undoes “${actionLabel(e.action_type)}”. ${ALSO_UNDOES[e.action_type] ?? ""}`}
                    />
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}

function recipient(m: OutboxMessage): string {
  if (m.channel === "whatsapp" || m.channel === "sms") return `Customer · ${maskPhone(m.recipient)}`;
  if (m.channel === "vendor") return `Vendor · ${m.recipient}`;
  if (m.channel === "courier") return `Courier · ${m.recipient}`;
  return m.recipient;
}
