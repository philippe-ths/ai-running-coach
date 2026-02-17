export interface CoachNextStep {
  action: string;
  details: string;
  why: string;
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
  key_takeaways: string[];
  next_steps: CoachNextStep[];
  risks: CoachRisk[];
  questions: CoachQuestion[];
}

export interface CoachReport {
  id: string;
  activity_id: string;
  report: CoachReportContent;
  meta: CoachReportMeta;
  created_at: string;
}
