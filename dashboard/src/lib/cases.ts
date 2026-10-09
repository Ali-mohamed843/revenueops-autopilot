import "server-only";

import { api } from "./api";
import type { CaseDetail } from "./types";

/** Case details for a handful of ids (approval queues, activity rows), fetched in parallel. */
export async function casesById(ids: string[]): Promise<Record<string, CaseDetail>> {
  const unique = [...new Set(ids)];
  const found = await Promise.all(unique.map((id) => api.case(id).catch(() => null)));
  return Object.fromEntries(found.filter((c): c is CaseDetail => c !== null).map((c) => [c.id, c]));
}

/** The decision engine's reasons a waiting action needs a person, from the case's plan. */
export function needsPersonReasons(c: CaseDetail | undefined, caseActionId: string) {
  const action = c?.actions.find((a) => a.id === caseActionId);
  return {
    action,
    reasons: (action?.tier_reasons ?? []).filter((r) => r.tier !== "auto"),
  };
}
