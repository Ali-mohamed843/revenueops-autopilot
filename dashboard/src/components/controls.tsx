"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { AlertCircle, CheckCircle2, Monitor, Moon, Sun } from "lucide-react";

import {
  actOnCase,
  approveExecution,
  completeExecution,
  investigateCase,
  planCase,
  rejectExecution,
  rollbackExecution,
  scanStore,
  setOperator,
  type Result,
} from "@/app/actions";
import { Button, cn } from "./ui";

// ---------------------------------------------------------------- feedback

function Feedback({ result, className }: { result: Result | null; className?: string }) {
  if (!result) return null;
  const Icon = result.ok ? CheckCircle2 : AlertCircle;
  return (
    <p
      role={result.ok ? "status" : "alert"}
      className={cn("flex items-start gap-1.5 text-[13px] leading-snug", result.ok ? "text-good" : "text-bad", className)}
    >
      <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>{result.message}</span>
    </p>
  );
}

function useAction() {
  const [pending, start] = useTransition();
  const [result, setResult] = useState<Result | null>(null);
  const runAction = (fn: () => Promise<Result>) => start(async () => setResult(await fn()));
  return { pending, result, runAction };
}

const fieldClass =
  "h-10 w-full rounded-[10px] border border-field bg-bg px-3 text-sm text-ink placeholder:text-faint focus:border-btn-line";

// -------------------------------------------------------------- navigation

export function NavLink({ href, children, badge }: { href: string; children: React.ReactNode; badge?: number }) {
  const path = usePathname();
  const active = href === "/" ? path === "/" : path.startsWith(href);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "inline-flex h-14 items-center gap-2 border-b-2 text-sm whitespace-nowrap transition-colors",
        active ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink",
      )}
    >
      {children}
      {badge ? <span className="num text-[12px] font-medium text-warn">{badge}</span> : null}
    </Link>
  );
}

// ------------------------------------------------------------------- theme

type Theme = "light" | "dark" | "system";
const THEMES: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "dark", icon: Moon, label: "Dark" },
  { value: "system", icon: Monitor, label: "System" },
];

export function ThemeToggle({ initial }: { initial: Theme }) {
  const [theme, setTheme] = useState<Theme>(initial);
  const current = THEMES.find((t) => t.value === theme) ?? THEMES[2];
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  const choose = (t: Theme) => {
    setTheme(t);
    if (t === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    document.cookie = `theme=${t}; path=/; max-age=31536000; samesite=lax`;
  };
  return (
    <button
      type="button"
      onClick={() => choose(next.value)}
      aria-label={`Theme: ${current.label}. Switch to ${next.label}`}
      title={`Theme: ${current.label}`}
      className="grid size-9 place-items-center rounded-full text-muted shadow-[inset_0_0_0_1px_var(--btn-line)] hover:bg-hover hover:text-ink"
    >
      <current.icon className="size-4" aria-hidden />
    </button>
  );
}

// ---------------------------------------------------------------- operator

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");

export function OperatorMenu({ name }: { name: string | null }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(name ?? "");
  const { pending, result, runAction } = useAction();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open, name]);

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={name ? `Acting as ${name}. Change name` : "Set your name for the audit trail"}
        title={name ? `Acting as ${name}` : "Set your name for the audit trail"}
        className="relative grid size-9 place-items-center rounded-full text-[12px] font-medium text-ink shadow-[inset_0_0_0_1px_var(--btn-line)] hover:bg-hover"
      >
        {name ? initials(name) : "?"}
        {!name && <span className="absolute end-0 top-0 size-2 rounded-full bg-warn-mark" aria-hidden />}
      </button>
      {open && (
        <form
          className="card absolute end-0 top-11 z-30 w-72 p-4 shadow-pop"
          onSubmit={(e) => {
            e.preventDefault();
            runAction(async () => {
              const r = await setOperator(value);
              if (r.ok) setOpen(false);
              return r;
            });
          }}
        >
          <label htmlFor="operator" className="text-[13px] text-muted">
            Your name, for the audit trail
          </label>
          <input
            id="operator"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="e.g. Ali"
            autoFocus
            className={cn(fieldClass, "mt-2")}
          />
          <p className="mt-2 text-[12px] text-muted">Every approval, rejection and undo is recorded under it.</p>
          <Button type="submit" variant="primary" size="sm" pending={pending} className="mt-3 w-full">
            Save
          </Button>
          <Feedback result={result} className="mt-2" />
        </form>
      )}
    </div>
  );
}

// -------------------------------------------------------- pipeline buttons

export function ScanButton() {
  const { pending, result, runAction } = useAction();
  return (
    <div className="flex flex-col items-end gap-2">
      <Button variant="primary" pending={pending} onClick={() => runAction(scanStore)}>
        {pending ? "Scanning the store…" : "Scan the store"}
      </Button>
      <Feedback result={result} />
    </div>
  );
}

const STEPS: Record<string, { label: string; busy: string; fn: (id: string) => Promise<Result> }> = {
  investigate: { label: "Investigate this case", busy: "Investigating · about a minute", fn: investigateCase },
  plan: { label: "Plan the actions", busy: "Planning · about a minute", fn: planCase },
  act: { label: "Take the next action", busy: "Acting…", fn: actOnCase },
};

export function CaseStepButton({ step, caseId }: { step: keyof typeof STEPS; caseId: string }) {
  const { pending, result, runAction } = useAction();
  const s = STEPS[step];
  return (
    <div className="flex flex-col items-end gap-2">
      <Button variant="amber" pending={pending} onClick={() => runAction(() => s.fn(caseId))}>
        {pending ? s.busy : s.label}
      </Button>
      <Feedback result={result} className="max-w-80 text-end" />
    </div>
  );
}

// -------------------------------------------------------- decisions

export function ApprovalControls({ executionId }: { executionId: string }) {
  const [note, setNote] = useState("");
  const { pending, result, runAction } = useAction();
  if (result?.ok) return <Feedback result={result} className="text-sm" />;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2.5">
        <Button variant="primary" pending={pending} onClick={() => runAction(() => approveExecution(executionId, note))}>
          Approve and run
        </Button>
        <Button disabled={pending} onClick={() => runAction(() => rejectExecution(executionId, note))}>
          Reject
        </Button>
        <label className="min-w-0 flex-[1_1_200px]">
          <span className="sr-only">Note</span>
          <input
            className={fieldClass}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Add a note (optional)"
          />
        </label>
      </div>
      <Feedback result={result} />
    </div>
  );
}

export function TaskControls({ executionId }: { executionId: string }) {
  const [note, setNote] = useState("");
  const { pending, result, runAction } = useAction();
  if (result?.ok) return <Feedback result={result} className="text-sm" />;
  return (
    <div className="w-full space-y-2">
      <div className="flex flex-wrap gap-2">
        <label className="min-w-0 flex-[1_1_220px]">
          <span className="sr-only">What happened</span>
          <input
            className={fieldClass}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What happened? e.g. “Buyer confirmed by phone”"
          />
        </label>
        <Button variant="primary" pending={pending} onClick={() => runAction(() => completeExecution(executionId, true, note))}>
          Done
        </Button>
        <Button disabled={pending} onClick={() => runAction(() => completeExecution(executionId, false, note))}>
          Couldn’t do it
        </Button>
      </div>
      <Feedback result={result} />
    </div>
  );
}

export function UndoControl({ executionId, label = "Undo", hint }: { executionId: string; label?: string; hint?: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const { pending, result, runAction } = useAction();
  if (result?.ok) return <Feedback result={result} />;
  if (!open) {
    return (
      <div className="space-y-2">
        <Button size="sm" onClick={() => setOpen(true)}>
          {label}
        </Button>
        <Feedback result={result} />
      </div>
    );
  }
  return (
    <div className="w-full space-y-2 rounded-[10px] bg-inset p-3">
      <p className="text-[13px] text-ink-2">
        {hint ?? "Undoing reverses the change in the store and cancels unsent messages."} Why?
      </p>
      <input
        className={fieldClass}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Reason, for the audit trail"
        autoFocus
      />
      <div className="flex gap-2">
        <Button size="sm" variant="danger" pending={pending} onClick={() => runAction(() => rollbackExecution(executionId, reason))}>
          {label}
        </Button>
        <Button size="sm" disabled={pending} onClick={() => setOpen(false)}>
          Keep it
        </Button>
      </div>
      <Feedback result={result} />
    </div>
  );
}
