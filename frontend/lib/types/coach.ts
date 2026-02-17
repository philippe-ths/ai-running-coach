export interface CoachTakeaway {
  text: string;
  evidence?: string | null;
}

export interface CoachNextStep {
  action: string;
  details: string;
  why: string;
  evidence?: string | null;
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
}

export interface CoachReportContent {
  key_takeaways: (CoachTakeaway | string)[];
  next_steps: CoachNextStep[];
  risks: CoachRisk[];
  questions: CoachQuestion[];
}

export interface CoachReportDebug {
  context_pack: Record<string, unknown>;
  system_prompt: string;
  raw_llm_response: string | null;
}

export interface CoachReport {
  id: string;
  activity_id: string;
  report: CoachReportContent;
  meta: CoachReportMeta;
  debug: CoachReportDebug;
  created_at: string;
}
