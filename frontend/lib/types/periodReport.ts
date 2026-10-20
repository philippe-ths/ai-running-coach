// #946: period reports — a considered review of a runner-chosen stretch of
// training, across the disciplines the runner chose. Mirrors backend
// app/schemas/period_report.py.

export type PeriodReportStatus = "generating" | "ready" | "failed";

export interface PeriodReportContent {
  message: string;
  headline: string | null;
  next_steps: string[];
}

export interface PeriodReport {
  id: string;
  period_start: string; // YYYY-MM-DD, inclusive
  period_end: string; // YYYY-MM-DD, inclusive
  disciplines: string[]; // empty = every discipline
  status: PeriodReportStatus;
  generated_at: string | null;
  created_at: string;
  // #974: when a failed report failed; null unless it did.
  failed_at: string | null;
  report: PeriodReportContent | null;
  // Runner-facing status/failure sentence, written for whoever is reading it —
  // never the gate's own internal text.
  message: string | null;
}

export interface PeriodReportListItem {
  id: string;
  period_start: string;
  period_end: string;
  disciplines: string[];
  status: PeriodReportStatus;
  generated_at: string | null;
  created_at: string;
  failed_at: string | null;
  headline: string | null;
}

export interface PeriodReportCreate {
  period_start: string;
  period_end: string;
  disciplines: string[];
}
