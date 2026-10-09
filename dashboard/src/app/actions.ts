"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";

import { api, ApiError } from "@/lib/api";
import { actionLabel, EXECUTION_STATUSES } from "@/lib/format";

export interface Result {
  ok: boolean;
  message: string;
}

const OPERATOR_COOKIE = "operator";

async function operator(): Promise<string> {
  const name = (await cookies()).get(OPERATOR_COOKIE)?.value?.trim();
  if (!name) throw new ApiError("Set your name first (the round button at the top right), so the audit trail shows who decided", 400);
  return name;
}

async function run(fn: () => Promise<string>): Promise<Result> {
  try {
    const message = await fn();
    revalidatePath("/", "layout");
    return { ok: true, message };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : String(e) };
  }
}

export async function setOperator(name: string): Promise<Result> {
  const clean = name.trim().slice(0, 100);
  if (!clean) return { ok: false, message: "Enter your name" };
  (await cookies()).set(OPERATOR_COOKIE, clean, { path: "/", sameSite: "lax", maxAge: 60 * 60 * 24 * 365 });
  revalidatePath("/", "layout");
  return { ok: true, message: `Acting as ${clean}` };
}

export async function approveExecution(id: string, note: string): Promise<Result> {
  return run(async () => {
    const ex = await api.approve(id, await operator(), note || undefined);
    return ex.status === "succeeded"
      ? `${actionLabel(ex.action_type)}: done`
      : `${actionLabel(ex.action_type)}: ${EXECUTION_STATUSES[ex.status].toLowerCase()}${ex.error ? ` (${ex.error})` : ""}`;
  });
}

export async function rejectExecution(id: string, note: string): Promise<Result> {
  return run(async () => {
    const ex = await api.reject(id, await operator(), note || undefined);
    return `${actionLabel(ex.action_type)}: rejected`;
  });
}

export async function completeExecution(id: string, succeeded: boolean, note: string): Promise<Result> {
  return run(async () => {
    if (!note.trim()) throw new ApiError("Say what happened, e.g. the outcome of the call", 400);
    const ex = await api.complete(id, await operator(), succeeded, note.trim());
    return `${actionLabel(ex.action_type)}: ${succeeded ? "marked done" : "marked as not working"}`;
  });
}

export async function rollbackExecution(id: string, reason: string): Promise<Result> {
  return run(async () => {
    if (!reason.trim()) throw new ApiError("Give a reason for undoing it", 400);
    const ex = await api.rollback(id, await operator(), reason.trim());
    return `${actionLabel(ex.action_type)}: undone`;
  });
}

export async function scanStore(): Promise<Result> {
  return run(async () => {
    const r = await api.detect();
    return `Found ${r.found} cases: ${r.opened} new, ${r.updated} updated, ${r.closed} closed`;
  });
}

export async function investigateCase(id: string): Promise<Result> {
  return run(async () => {
    await api.investigate(id);
    return "Investigation finished";
  });
}

export async function planCase(id: string): Promise<Result> {
  return run(async () => {
    await api.plan(id);
    return "Plan ready";
  });
}

export async function actOnCase(id: string): Promise<Result> {
  return run(async () => {
    const r = await api.act(id);
    if (r.executions.length === 0) return r.waiting ? "Nothing is ready yet: the next step is waiting" : "Nothing to do";
    return r.executions
      .map((e) => `${actionLabel(e.action_type)}: ${EXECUTION_STATUSES[e.status].toLowerCase()}`)
      .join("; ");
  });
}

export async function runDryRun(): Promise<Result> {
  return run(async () => {
    await api.dryRun();
    return "Dry run finished. Nothing was executed.";
  });
}

export async function runOutcomes(episodes: number, seed: number): Promise<Result> {
  return run(async () => {
    const r = await api.simulateOutcomes(episodes, seed);
    return `Simulated ${episodes.toLocaleString("en-US")} outcomes (seed ${r.seed}).`;
  });
}

export async function runCalibrate(): Promise<Result> {
  return run(async () => {
    const r = await api.calibrate();
    return `Calibration ${r.id.slice(0, 8)} is now used for scoring.`;
  });
}
