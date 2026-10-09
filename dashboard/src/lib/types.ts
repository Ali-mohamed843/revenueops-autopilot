// Shapes returned by the agent service API (agent-service/src/revenueops/main.py).

export type CaseType =
  | "refusal_risk"
  | "unconfirmed_order"
  | "stalled_fulfilment"
  | "late_shipment"
  | "abandoned_cart"
  | "stale_return";

export type CaseStatus =
  | "open"
  | "investigated"
  | "investigation_failed"
  | "planned"
  | "planning_failed"
  | "acting"
  | "acted"
  | "closed";

export type Tier = "auto" | "approval" | "human_only";

export type ExecutionStatus =
  | "pending_approval"
  | "awaiting_human"
  | "rejected"
  | "succeeded"
  | "failed"
  | "rolled_back";

export interface CaseSummary {
  id: string;
  store: string;
  case_type: CaseType;
  subject_type: "order" | "cart" | "return";
  subject_id: string;
  status: CaseStatus;
  title: string;
  value_at_risk: string;
  currency: string;
  priority: number;
  signals: Record<string, unknown>;
  detected_at: string;
  last_seen_at: string;
  closed_at: string | null;
  close_reason: string | null;
}

export interface Evidence {
  fact: string;
  source: string;
}

export interface InvestigationReport {
  summary: string;
  likely_cause: string;
  evidence: Evidence[];
  risk_factors: string[];
  mitigating_factors: string[];
  missing_information: string[];
  confidence: number;
}

export interface AgentRunMeta {
  model: string;
  turns: number;
  duration_ms: number;
  usage: { input_tokens: number; output_tokens: number };
}

export interface TierReason {
  tier: Tier;
  text: string;
  rule: string | null;
}

export interface CaseAction {
  id: string;
  rank: number;
  recommended: boolean;
  action_type: string;
  params: Record<string, unknown>;
  rationale: string;
  policy_refs: string[];
  p_with: number;
  p_without: number;
  cost: string;
  expected_value: string;
  tier: Tier;
  tier_reasons: TierReason[];
  ready: boolean;
  waiting_for: string | null;
  status: string;
}

export interface OutboxMessage {
  id: string;
  execution_id: string;
  case_id: string;
  channel: string;
  recipient: string;
  language: string;
  body: string;
  drafted_by: string;
  status: "queued" | "cancelled";
  created_at: string;
  cancelled_at: string | null;
}

export interface Execution {
  id: string;
  case_id: string;
  case_action_id: string;
  action_type: string;
  params: Record<string, unknown>;
  tier: Tier;
  status: ExecutionStatus;
  reversible: boolean;
  policy_refs: string[];
  confidence: number | null;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  finished_at: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error: string | null;
  rolled_back_by: string | null;
  rolled_back_at: string | null;
  rollback_result: Record<string, unknown> | null;
  messages: OutboxMessage[];
}

export interface CaseEvent {
  type: string;
  actor: string;
  data: Record<string, unknown>;
  at: string;
}

export interface CaseDetail extends CaseSummary {
  investigation: ({ report: InvestigationReport } & AgentRunMeta) | null;
  plan: ({ approach: string } & AgentRunMeta) | null;
  actions: CaseAction[];
  next_action_id: string | null; // what "Take next action" would do now
  executions: Execution[];
  events: CaseEvent[];
}

export interface Bucket {
  cases: number;
  value: string;
}

export interface Stats {
  currency: string;
  days: number;
  from: string;
  to: string;
  open: Bucket;
  recovered: Bucket;
  cleared_without_action: Bucket;
  actions: {
    auto: number;
    approved: number;
    by_person: number;
    rejected: number;
    failed: number;
    rolled_back: number;
    pending_approval: number;
    awaiting_human: number;
  };
  automation_rate: number | null;
  by_type: ({ case_type: CaseType } & Bucket)[];
  by_status: { status: CaseStatus; cases: number }[];
  by_governorate: ({ governorate: string } & Bucket)[];
  other_governorates: Bucket;
  risk_scores: { from: number; to: number; cases: number }[];
  daily: {
    date: string;
    detected_cases: number;
    detected_value: string;
    recovered_value: string;
    auto: number;
    approved: number;
    by_person: number;
  }[];
  llm: { input_tokens: number; output_tokens: number; agent_runs: number };
}

export interface Policy {
  id: string;
  title: string;
  applies_to: CaseType[];
  rules: { id: string; text: string }[];
}
