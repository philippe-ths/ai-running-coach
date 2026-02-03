// ── ContextPack (mirrors backend ContextPack) ─────────────────────

export interface CPActivity {
  id: string;
  start_time: string;
  type: string;
  name?: string;
  distance_m: number;
  moving_time_s: number;
  elapsed_time_s?: number;
  avg_pace_s_per_km?: number;
  avg_hr?: number;
  max_hr?: number;
  avg_cadence?: number;
  elevation_gain_m?: number;
}

export interface CPAthlete {
  goal?: string;
  experience_level?: string;
  injury_notes?: string;
  age?: number;
  sex?: string;
}

export interface CPDerivedMetric {
  key: string;
  value: number;
  unit?: string;
  confidence: number; // 0..1
  evidence: string;
}

export interface CPFlag {
  code: string;
  severity: "info" | "warn" | "risk";
  message: string;
  evidence: string;
}

export interface CPLast7Days {
  total_distance_m?: number;
  total_time_s?: number;
  intensity_summary: string;
  load_trend: string;
}

export interface CPCheckIn {
  rpe_0_10?: number;
  pain_0_10?: number;
  sleep_0_10?: number;
  notes?: string;
}

export interface ContextPack {
  activity: CPActivity;
  athlete: CPAthlete;
  derived_metrics: CPDerivedMetric[];
  flags: CPFlag[];
  last_7_days: CPLast7Days;
  check_in: CPCheckIn;
  available_signals: string[];
  missing_signals: string[];
  generated_at_iso: string;
}

// ── Existing types ─────────────────────────────────────────────────

export interface DerivedMetric {
  activity_class: string;
  effort_score: number;
  flags: string[];
  confidence: string;
  confidence_reasons: string[];
  pace_variability?: number;
  hr_drift?: number;
  time_in_zones?: Record<string, number>;
}

export interface CoachReportData {
    headline: string;
    session_type: string;
    intent_vs_execution: string[];
    key_metrics: string[];
    strengths: string[];
    opportunities: string[];
    next_run: {
        duration: string;
        type: string;
        intensity: string;
        description: string;
        duration_minutes?: number;
        intensity_target?: string;
    };
    weekly_focus: string[];
    warnings: string[];
    one_question?: string;
}


// ── Coach Verdict V3 ────────────────────────────────────────────────
export interface V3Headline {
    sentence: string;
    status: "green" | "amber" | "red";
}

export interface V3ScorecardItem {
    item: "Purpose match" | "Control (smoothness)" | "Aerobic value" | "Mechanical quality" | "Risk / recoverability";
    rating: "ok" | "warn" | "fail" | "unknown";
    reason: string;
}

export interface V3RunStory {
    start: string;
    middle: string;
    finish: string;
}

export interface V3Lever {
    category: "pacing" | "physiology" | "mechanics" | "context";
    signal: string;
    cause: string;
    fix: string;
    cue: string;
}

export interface V3NextSteps {
    tomorrow: string;
    next_7_days: string;
}

export interface CoachVerdictV3 {
    inputs_used_line: string;
    headline: V3Headline;
    why_it_matters: string[];
    scorecard: V3ScorecardItem[];
    run_story: V3RunStory;
    lever: V3Lever;
    next_steps: V3NextSteps;
    question_for_you?: string;
}

export interface Advice {
    verdict: string;
    evidence: string[];
    next_run: any;
    week_adjustment: string;
    warnings: string[];
    question?: string;
    full_text: string;
    structured_report?: CoachReportData | CoachVerdictV3; // Can theoretically allow V3 here in specific implementation
}

export interface ActivityStream {
    stream_type: string;
    data: any[];
}

export interface Activity {
  id: string;
  name: string;
  start_date: string;
  distance_m: number;
  moving_time_s: number;
  avg_hr?: number;
  max_hr?: number;
  elev_gain_m: number;
  user_intent?: string;
  metrics?: DerivedMetric;
  raw_summary?: any;
  streams?: ActivityStream[];
  advice?: Advice;
  check_in?: {
    rpe: number;
    pain_score: number;
    notes?: string;
  };
}
