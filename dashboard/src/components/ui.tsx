import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { Loader2 } from "lucide-react";

import { CASE_STATUSES, EXECUTION_STATUSES, TIERS, toNumber } from "@/lib/format";
import type { CaseStatus, ExecutionStatus, Tier } from "@/lib/types";

export const cn = (...classes: (string | false | null | undefined)[]) => classes.filter(Boolean).join(" ");

// ------------------------------------------------------------------ layout

export function Card({ className, children, ...rest }: ComponentProps<"section">) {
  return (
    <section className={cn("card", className)} {...rest}>
      {children}
    </section>
  );
}

export function CardTitle({ title, subtitle, action }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-[15px] font-medium text-ink">{title}</h2>
        {subtitle && <p className="mt-1 text-[13px] text-muted">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function PageTitle({
  title,
  subtitle,
  eyebrow,
  side,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  eyebrow?: ReactNode;
  side?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-6">
      <div className="min-w-0 flex-[1_1_520px]">
        {eyebrow && <p className="mb-2.5 text-[13px] text-muted">{eyebrow}</p>}
        <h1 className="serif text-[34px] leading-[1.1] font-normal tracking-[-0.015em] text-ink sm:text-[44px]">{title}</h1>
        {subtitle && <p className="mt-3 max-w-[660px] text-[15px] leading-[1.55] text-ink-2">{subtitle}</p>}
      </div>
      {side}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-6 py-14 text-center">
      <p className="serif text-xl text-ink">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-sm text-[13px] text-muted">{children}</div>}
    </div>
  );
}

// ----------------------------------------------------------------- buttons

const buttonStyles = {
  primary: "bg-primary text-primary-ink hover:bg-primary-hover",
  secondary: "bg-transparent text-ink shadow-[inset_0_0_0_1px_var(--btn-line)] hover:bg-hover hover:text-ink-strong",
  amber: "bg-warn-mark text-[#1e1405] hover:brightness-105",
  danger: "bg-transparent text-bad shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--bad)_50%,transparent)] hover:bg-bad-soft",
};

export function buttonClass(variant: keyof typeof buttonStyles = "secondary", size: "sm" | "md" = "md") {
  return cn(
    "inline-flex items-center justify-center gap-2 rounded-[10px] font-medium whitespace-nowrap transition-colors",
    "disabled:pointer-events-none disabled:opacity-50",
    size === "sm" ? "h-[34px] px-3 text-[13px]" : "h-10 px-4 text-sm",
    buttonStyles[variant],
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  pending,
  className,
  children,
  ...rest
}: ComponentProps<"button"> & { variant?: keyof typeof buttonStyles; size?: "sm" | "md"; pending?: boolean }) {
  return (
    <button type="button" className={cn(buttonClass(variant, size), className)} disabled={pending || rest.disabled} {...rest}>
      {pending && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function ButtonLink({
  variant = "secondary",
  size = "md",
  className,
  ...rest
}: ComponentProps<typeof Link> & { variant?: keyof typeof buttonStyles; size?: "sm" | "md" }) {
  return <Link className={cn(buttonClass(variant, size), className)} {...rest} />;
}

// ------------------------------------------------------------------ status
// Colour always comes with its label; the dot is decoration.

export type Tone = "good" | "warn" | "bad" | "info" | "muted" | "ink";

const toneText: Record<Tone, string> = {
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  info: "text-info",
  muted: "text-muted",
  ink: "text-ink-2",
};

export const toneDot: Record<Tone | "new" | "planned", string> = {
  good: "var(--good-mark)",
  warn: "var(--warn-mark)",
  bad: "var(--bad)",
  info: "var(--c2)",
  muted: "var(--muted)",
  ink: "var(--muted)",
  new: "var(--c5)",
  planned: "var(--c4)",
};

export function StatusText({
  tone,
  dot,
  children,
  className,
}: {
  tone: Tone;
  dot?: string; // CSS colour for the dot; none = no dot
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-[13px] whitespace-nowrap", toneText[tone], className)}>
      {dot && <span className="size-1.5 shrink-0 rounded-full" style={{ background: dot }} aria-hidden />}
      {children}
    </span>
  );
}

export function caseStatusStyle(status: CaseStatus): { tone: Tone; dot: string } {
  switch (status) {
    case "acting":
      return { tone: "warn", dot: toneDot.warn };
    case "acted":
      return { tone: "ink", dot: toneDot.good };
    case "investigated":
      return { tone: "ink", dot: toneDot.info };
    case "planned":
      return { tone: "ink", dot: toneDot.planned };
    case "investigation_failed":
    case "planning_failed":
      return { tone: "bad", dot: toneDot.bad };
    case "closed":
      return { tone: "muted", dot: toneDot.muted };
    default:
      return { tone: "ink", dot: toneDot.new };
  }
}

export function CaseStatusText({ status, className }: { status: CaseStatus; className?: string }) {
  const s = caseStatusStyle(status);
  return (
    <StatusText tone={s.tone} dot={s.dot} className={className}>
      {CASE_STATUSES[status].label}
    </StatusText>
  );
}

export function executionTone(status: ExecutionStatus): Tone {
  if (status === "succeeded") return "good";
  if (status === "failed") return "bad";
  if (status === "pending_approval" || status === "awaiting_human") return "warn";
  return "muted";
}

export function ExecutionStatusText({ status, className }: { status: ExecutionStatus; className?: string }) {
  return <StatusText tone={executionTone(status)} className={cn("text-[12px]", className)}>{EXECUTION_STATUSES[status]}</StatusText>;
}

export function TierText({ tier, className }: { tier: Tier; className?: string }) {
  return (
    <span title={TIERS[tier].blurb} className={cn("text-[12px]", tier === "auto" ? "text-good" : "text-warn", className)}>
      {TIERS[tier].label}
    </span>
  );
}

export function RuleChip({ id, text }: { id: string; text?: string }) {
  return (
    <Link
      href={`/policies#${id}`}
      title={text ?? id}
      className="inline-block rounded-[5px] px-[7px] py-0.5 font-mono text-[12px] whitespace-nowrap text-ink-2 shadow-[inset_0_0_0_1px_var(--btn-line)] hover:text-ink"
    >
      {id}
    </Link>
  );
}

// --------------------------------------------------------------- navigation

export function Segmented({
  label,
  items,
}: {
  label: string;
  items: { href: string; label: string; active: boolean; count?: number; countTone?: Tone }[];
}) {
  return (
    <nav
      aria-label={label}
      className="flex flex-wrap gap-0.5 rounded-[10px] bg-[color-mix(in_srgb,var(--line)_45%,transparent)] p-[3px] shadow-[inset_0_0_0_1px_var(--line)]"
    >
      {items.map((i) => (
        <Link
          key={i.href + i.label}
          href={i.href}
          aria-current={i.active ? "page" : undefined}
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-[7px] px-3 text-[13px] transition-colors",
            i.active ? "bg-card text-ink shadow-card" : "text-muted hover:text-ink",
          )}
        >
          {i.label}
          {i.count !== undefined && <span className={cn("num", i.countTone ? toneText[i.countTone] : "text-muted")}>{i.count}</span>}
        </Link>
      ))}
    </nav>
  );
}

// ------------------------------------------------------------------ figures

/** 2,216,686.30 with the decimals quieter, as in the design. */
export function Money({
  value,
  size,
  currency = "EGP",
  className,
}: {
  value: string | number;
  size: "hero" | "large" | "medium";
  currency?: string | null;
  className?: string;
}) {
  const [whole, cents] = toNumber(value)
    .toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    .split(".");
  const big = { hero: "text-[56px] sm:text-[96px] tracking-[-0.02em]", large: "text-[48px] sm:text-[64px]", medium: "text-[30px]" }[size];
  const small = { hero: "text-[28px] sm:text-[44px]", large: "text-[24px] sm:text-[32px]", medium: "text-[18px]" }[size];
  return (
    <span className={cn("serif num inline-flex flex-wrap items-baseline gap-x-3 leading-none", className)}>
      <span>
        <span className={cn(big, "font-light")}>{whole}</span>
        <span className={cn(small, "font-light text-faint")}>.{cents}</span>
      </span>
      {currency && <span className="font-sans text-[15px] text-muted">{currency}</span>}
    </span>
  );
}

/** A thin bar, 0..1, as the design's confidence and probability bars. */
export function Bar({ value, color = "var(--c2)", width = "100%", label }: { value: number; color?: string; width?: string; label: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      className="relative block h-[3px] bg-track"
      style={{ width }}
    >
      <span className="absolute inset-y-0 start-0" style={{ width: `${pct}%`, background: color }} />
    </span>
  );
}
