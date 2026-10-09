import type { CaseStatus, CaseType, ExecutionStatus, Tier } from "./types";

const nf = (options: Intl.NumberFormatOptions) => new Intl.NumberFormat("en-US", options);
const fullMoney = nf({ minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = nf({ notation: "compact", maximumFractionDigits: 1 });
const integer = nf({ maximumFractionDigits: 0 });

export const toNumber = (v: string | number | null | undefined) => (v == null ? 0 : Number(v));

/** 41,084.30 EGP */
export const money = (v: string | number, currency = "EGP") => `${fullMoney.format(toNumber(v))} ${currency}`;

/** 41.1K EGP - for tiles and axis ticks; the full value stays in tables and tooltips. */
export const moneyCompact = (v: string | number, currency = "EGP") => {
  const n = toNumber(v);
  return `${Math.abs(n) < 1000 ? integer.format(n) : compact.format(n)} ${currency}`;
};

export const count = (n: number) => integer.format(n);
export const compactCount = (n: number) => (n < 1000 ? integer.format(n) : compact.format(n));
export const percent = (r: number | null | undefined, digits = 0) =>
  r == null ? "—" : `${(r * 100).toFixed(digits)}%`;

export const shortId = (id: string) => id.slice(0, 8);

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const seconds = (new Date(iso).getTime() - now) / 1000;
  const abs = Math.abs(seconds);
  if (abs < 60) return "just now";
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(seconds / 86400), "day");
  return dateTime(iso);
}

export const dateTime = (iso: string | null | undefined) =>
  iso
    ? new Date(iso).toLocaleString("en-GB", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

export const shortDate = (isoDate: string) =>
  new Date(`${isoDate}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

export const CASE_TYPES: Record<CaseType, { label: string; blurb: string }> = {
  refusal_risk: { label: "Refusal risk", blurb: "Risky COD order not shipped yet" },
  unconfirmed_order: { label: "Unconfirmed order", blurb: "Pending 24h+ without confirmation" },
  stalled_fulfilment: { label: "Stalled fulfilment", blurb: "Confirmed but not shipped in 48h" },
  late_shipment: { label: "Late shipment", blurb: "On the road past the courier's promise" },
  abandoned_cart: { label: "Abandoned cart", blurb: "Items left in a cart for a day or more" },
  stale_return: { label: "Stale return", blurb: "Return request untouched for 48h" },
};

export const CASE_STATUSES: Record<CaseStatus, { label: string; stage: number }> = {
  open: { label: "New", stage: 0 },
  investigation_failed: { label: "Investigation failed", stage: 0 },
  investigated: { label: "Investigated", stage: 1 },
  planning_failed: { label: "Planning failed", stage: 1 },
  planned: { label: "Planned", stage: 2 },
  acting: { label: "Waiting for a person", stage: 3 },
  acted: { label: "Action taken", stage: 3 },
  closed: { label: "Closed", stage: 4 },
};

export const TIERS: Record<Tier, { label: string; blurb: string }> = {
  auto: { label: "Automatic", blurb: "Runs on its own: harmless if wrong, or can be undone" },
  approval: { label: "Needs approval", blurb: "Runs once a person approves it" },
  human_only: { label: "Person only", blurb: "A person decides and carries it out" },
};

export const EXECUTION_STATUSES: Record<ExecutionStatus, string> = {
  pending_approval: "Waiting for approval",
  awaiting_human: "Waiting for a person",
  rejected: "Rejected",
  succeeded: "Done",
  failed: "Failed",
  rolled_back: "Undone",
};

export const ACTIONS: Record<string, string> = {
  send_confirmation_reminder: "Send a confirmation reminder",
  request_phone_confirmation: "Confirm by phone",
  hold_dispatch: "Hold dispatch",
  request_deposit: "Ask for a deposit",
  recommend_cancellation: "Recommend cancelling",
  nudge_vendor: "Nudge the vendor",
  notify_customer_of_delay: "Tell the customer about the delay",
  open_courier_ticket: "Open a courier ticket",
  send_cart_reminder: "Send a cart reminder",
  offer_discount: "Offer a discount code",
  process_return: "Process the return",
};

export const actionLabel = (key: string) => ACTIONS[key] ?? key.replaceAll("_", " ");

export const EVENT_LABELS: Record<string, string> = {
  detected: "Detected",
  reclassified: "Reclassified",
  closed: "Closed",
  investigated: "Investigated",
  investigation_failed: "Investigation failed",
  planned: "Planned",
  planning_failed: "Planning failed",
  action_requested: "Action requested",
  action_approved: "Approved",
  action_rejected: "Rejected",
  action_succeeded: "Action done",
  action_failed: "Action failed",
  action_completed: "Reported back",
  action_rolled_back: "Undone",
};

export const SOURCE_LABELS: Record<string, string> = {
  case_signals: "Detector",
  order: "Order",
  status_history: "Status history",
  shipment: "Shipment",
  delivery_attempts: "Delivery attempts",
  confirmations: "Confirmations",
  returns: "Returns",
  customer_profile: "Customer history",
};

export const humanize = (key: string) => key.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());

/** Params as readable text: {"channel":"sms","percent":10} -> "sms · 10%" */
export function paramsText(params: Record<string, unknown>): string {
  return Object.entries(params)
    .map(([k, v]) => (k === "percent" ? `${v}%` : k.endsWith("_hours") ? `${v}h` : k.endsWith("_days") ? `${v} days` : String(v)))
    .join(" · ");
}

// ------------------------------------------------------------- Cairo time
// The store runs on Cairo time; the server may not.
const TZ = "Africa/Cairo";

const cairoParts = (d: Date) =>
  Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "numeric", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit" })
      .formatToParts(d)
      .map((p) => [p.type, p.value]),
  );

export function greeting(now = new Date()): string {
  const hour = Number(cairoParts(now).hour);
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

/** Thursday, 8 October */
export const longDate = (d: Date | string = new Date()) =>
  new Date(d).toLocaleDateString("en-GB", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" });

/** 09:30 */
export const clock = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }) : "—";

/** 2026-10-08 in Cairo, for grouping by day. */
export const cairoDay = (iso: string) => {
  const p = cairoParts(new Date(iso));
  return `${p.year}-${p.month}-${p.day}`;
};

/** "Today, 9 October" / "Yesterday, 8 October" / "Tuesday, 6 October" */
export function dayHeading(day: string, now = new Date()): string {
  const today = cairoDay(now.toISOString());
  const yesterday = cairoDay(new Date(now.getTime() - 86_400_000).toISOString());
  const date = new Date(`${day}T12:00:00Z`);
  const dayMonth = date.toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "long" });
  if (day === today) return `Today, ${dayMonth}`;
  if (day === yesterday) return `Yesterday, ${dayMonth}`;
  return date.toLocaleDateString("en-GB", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" });
}

/** "3 h", "12 min", "2 days" */
export function age(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const minutes = Math.max(0, (now - new Date(iso).getTime()) / 60000);
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))} min`;
  if (minutes < 60 * 48) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} days`;
}

// ------------------------------------------------------------- subjects

/** "order 36506eb9", "cart 9eca1121", "return 1f2e3d4c" */
export const subjectLabel = (type: string, id: string) => `${type} ${shortId(id)}`;

/** 01099000110 -> "010 •••• 0110": enough to recognise, not to copy. */
export function maskPhone(phone: string): string {
  const digits = phone.replace(/[^\d+]/g, "");
  if (digits.length < 8) return phone;
  return `${digits.slice(0, 3)} •••• ${digits.slice(-4)}`;
}
