/**
 * P1.3 Coaching stance — the runner-declared stance (ADR 0015).
 *
 * The runner picks a school of training thought (from the corpus) and sets two
 * 1-5 emphasis dials (Data↔Sentiment, Process↔Outcome). The catalog (schools +
 * axes + the default) comes from the backend so the picker renders from one source
 * of truth. Stance carries NO free-text — runner-supplied philosophy is User
 * materials (a later milestone), not stance.
 */

export interface StanceSelection {
  school: string | null;
  data_sentiment: number | null;
  process_outcome: number | null;
}

export interface StanceSchoolInfo {
  key: string;
  name: string;
  stance: string;
}

export interface StanceAxisInfo {
  key: string;
  low_pole: string;
  high_pole: string;
}

export interface StanceCatalog {
  schools: StanceSchoolInfo[];
  axes: StanceAxisInfo[];
  default_school: string;
  default_data_sentiment: number;
  default_process_outcome: number;
}

export interface StanceConfig {
  current: StanceSelection;
  catalog: StanceCatalog;
}

export type EmphasisKey = "data_sentiment" | "process_outcome";
