export interface EvidenceRef {
  field: string;
  value: unknown;
}

export interface CoachTakeaway {
  text: string;
  evidence?: EvidenceRef[] | string | null;
}

export interface CoachNextStep {
  action: string;
  details: string;
  why: string;
  evidence?: EvidenceRef[] | string | null;
}

export interface CoachRisk {
  flag: string;
  explanation: string;
  mitigation: string;
}

export interface CoachQuestion {
  question: string;
  reason: string;
}

export interface CoachReportMeta {
  confidence: "low" | "medium" | "high";
  model_id: string;
  prompt_id: string;
  schema_version: string;
  input_hash: string;
  generated_at: string;
  policy_violations?: string[];
  // A3: a prose message was produced but its structured tail was missing/unusable.
  tail_degraded?: boolean;
}

export interface CoachReportContent {
  headline?: string;
  thesis?: string;
  lead_argument?: CoachTakeaway;
  key_takeaways: (CoachTakeaway | string)[];
  next_steps: CoachNextStep[];
  risks: CoachRisk[];
  questions: CoachQuestion[];
}

// A3 (ADR 0009): a tappable quick-reply on a coach question. `kind` tells the UI
// how to handle a tap; A3 renders the label only (inline-keyboard delivery is I1).
export interface TappableOption {
  id: string;
  label: string;
  kind: "rpe" | "pain" | "reply" | "dispute" | "custom";
  payload?: unknown;
}

export interface CoachMessageQuestion {
  question: string;
  reason: string;
  options: TappableOption[];
}

// A3 (ADR 0009): the prose-message output (schema 2.0). `message` is the product;
// the rest is the thin structured tail (affordances + memory hooks only).
export interface CoachMessageReport {
  message: string;
  headline?: string;
  next_steps: CoachNextStep[];
  risks: CoachRisk[];
  questions: CoachMessageQuestion[];
  tail_degraded: boolean;
}

// The stored report is one of two shapes, keyed by schema-version family. The
// presence of `message` discriminates the A3 prose shape from the legacy one.
export type CoachReportBody = CoachReportContent | CoachMessageReport;

export function isMessageReport(
  body: CoachReportBody,
): body is CoachMessageReport {
  return (body as CoachMessageReport).message !== undefined;
}

export interface CoachReportDebug {
  context_pack: Record<string, unknown>;
  system_prompt: string;
  raw_llm_response: string | null;
}

export interface CoachReport {
  id: string;
  activity_id: string;
  report: CoachReportBody;
  meta: CoachReportMeta;
  debug: CoachReportDebug;
  created_at: string;
}
