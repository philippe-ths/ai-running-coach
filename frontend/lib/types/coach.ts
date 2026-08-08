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
  // P1.1: this report was generated under a different voice than the runner's
  // current one, so it should regenerate to honour the new voice. A read-time flag
  // set by the backend; the panel auto-triggers the async regen once when it is true.
  voice_stale?: boolean;
  // #646: non-destructive-regen provenance. Set only when this report was produced by
  // a "Re-run" that superseded a prior report. `regenerated_at` is when it re-ran;
  // `memory_as_of` is the runner-memory as-of date it spoke from. The panel shows a
  // "Regenerated ..., memory as of ..." stamp so a report re-run with hindsight is honest.
  regenerated_at?: string | null;
  memory_as_of?: string | null;
  // #822: the character this report speaks in ("The Cornerman", "The Cornerman
  // (adjusted)", "Custom"), stamped at generation — so an older report names the
  // voice that wrote it rather than whatever is selected today. Null means Default.
  voice_name?: string | null;
  // #824: what the rewrite did — "applied", or why the baseline stands.
  voice_rewrite?: string | null;
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
// A4 (ADR 0010): a two-stage Exchange rides one evolving report. `opener_message`
// holds the immediate stage-one reaction; `message` holds the conditional fuller
// turn. An opener-only row has `opener_message` set and `message` empty (the
// fuller has not landed yet). `schedule_fuller_turn` is the opener's depth bit.
export interface CoachMessageReport {
  message: string;
  headline?: string;
  next_steps: CoachNextStep[];
  risks: CoachRisk[];
  questions: CoachMessageQuestion[];
  tail_degraded: boolean;
  opener_message?: string | null;
  schedule_fuller_turn?: boolean;
  // #822: the report is generated voiceless, then said again in the runner's
  // chosen voice. `message`/`opener_message` are always the voiceless baseline;
  // these carry the voiced rendering when the runner declared a voice and the
  // rewrite held. Null means Default, or a rewrite that degraded to the baseline.
  // The runner reads the voiced text; keeping both is what makes voice auditable.
  voiced_message?: string | null;
  voiced_opener_message?: string | null;
}

// The prose the RUNNER reads for a stage: the voiced rendering when there is one,
// else the baseline. One helper so no surface can drift into showing the
// unvoiced text to a runner who chose a voice.
export function runnerFacingProse(
  body: CoachMessageReport,
  stage: 'opener' | 'fuller',
): string {
  const text =
    stage === 'opener'
      ? body.voiced_opener_message ?? body.opener_message
      : body.voiced_message ?? body.message;
  return (text ?? '').trim();
}

// The stored report is one of two shapes, keyed by schema-version family. The
// presence of `message` discriminates the A3 prose shape from the legacy one.
export type CoachReportBody = CoachReportContent | CoachMessageReport;

export function isMessageReport(
  body: CoachReportBody,
): body is CoachMessageReport {
  return (body as CoachMessageReport).message !== undefined;
}

// A4: an opener-only message report — the opener prose is present but the fuller
// turn has not yet filled `message`. The panel renders the opener and waits for
// the fuller turn (which lands later, server-side).
export function isOpenerOnly(body: CoachReportBody): boolean {
  if (!isMessageReport(body)) return false;
  return Boolean(body.opener_message) && !(body.message ?? "").trim();
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
