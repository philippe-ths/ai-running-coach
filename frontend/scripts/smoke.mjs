import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";

// Pick ports dynamically. Fixed 3001/3100 collided with this project's
// aiw-grafana / aiw-loki containers and caused the smoke to lock onto the
// wrong server via fetch keep-alive.
async function pickFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((err) => (err ? reject(err) : resolve(port)));
    });
  });
}

const MOCK_API_PORT = await pickFreePort();
const FRONTEND_PORT = await pickFreePort();
const MOCK_API_BASE_URL = `http://127.0.0.1:${MOCK_API_PORT}`;
const FRONTEND_BASE_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

const mockActivity = {
  id: "42",
  name: "Morning Tempo",
  type: "Run",
  start_date: "2026-03-28T07:00:00Z",
  distance_m: 10250,
  moving_time_s: 2820,
  elapsed_time_s: 2880,
  elev_gain_m: 85,
  avg_hr: 158,
  avg_cadence: 176,
  raw_summary: {
    sport_type: "Run",
    type: "Run",
    elapsed_time: 2880,
    max_heartrate: 172,
    suffer_score: 48,
    average_watts: 255,
    weighted_average_watts: 268,
    kilojoules: 719,
    device_name: "Forerunner",
  },
  check_in: null,
  user_intent: null,
  // #944: the activity is ANALYSED. A coach report only exists for an activity
  // that has metrics, and the report panel renders nothing without them — so the
  // smoke's activity had never once exercised the panel. Minimal but real: the
  // classification axes the headline reads, and the two fields the panel's own
  // caption depends on.
  metrics: {
    headline: "Tempo run",
    effort: "hard",
    duration_class: "standard",
    structure: "continuous",
    is_hilly: false,
    is_race: false,
    effort_score: 62,
    flags: [],
    confidence: "high",
    confidence_reasons: [],
    pace_variability: 0.06,
    hr_drift: 2.4,
    hr_zones_source: "strava",
  },
  streams: [],
  splits: [],
};

// #947: the day-grouped activity list. Built so the "All Activities" page's
// real PAGE_SIZE (20) boundary lands mid-day: the first page (skip=0) returns
// exactly 20 rows ending partway through the OLDEST day below, and the second
// page (skip=20) returns that day's remaining rows plus the pagination
// terminator — so the smoke exercises both the boundary-day "Partial" mark
// (while only page 1 is loaded) and the cross-page merge (once page 2 lands).
// One day is deliberately mixed-discipline (a run, a ride, and a strength
// session with no `effort_score` yet) to exercise the discipline dot and the
// "one activity not yet analysed" partial-load reading.
function listActivity(id, name, type, dateIso, timeLocal, {
  movingTimeS = 2400,
  distanceM = 6000,
  effortScore = 40,
} = {}) {
  return {
    id,
    name,
    type,
    start_date: `${dateIso}T${timeLocal}:00Z`,
    start_date_local: `${dateIso}T${timeLocal}:00`,
    distance_m: distanceM,
    moving_time_s: movingTimeS,
    elapsed_time_s: movingTimeS + 60,
    elev_gain_m: 20,
    avg_hr: 148,
    avg_cadence: 172,
    headline: null,
    coach_lead: null,
    effort_score: effortScore,
  };
}

const mockActivityListPage = [
  listActivity("l01", "Morning Tempo", "Run", "2026-03-28", "07:00", { effortScore: 45 }),
  // The mixed day: a run, a ride, and a strength session with NO effort_score
  // yet (still awaiting analysis) — the day's LOAD total must read as partial.
  listActivity("l02", "Easy Shakeout", "Run", "2026-03-27", "06:45", { effortScore: 30, distanceM: 5000 }),
  listActivity("l03", "Recovery Spin", "Ride", "2026-03-27", "12:30", { effortScore: 18, distanceM: 15000, movingTimeS: 2700 }),
  { ...listActivity("l04", "Lower Body", "WeightTraining", "2026-03-27", "18:15", { distanceM: 0, movingTimeS: 2400 }), effort_score: null },
  listActivity("l05", "Steady Run", "Run", "2026-03-26", "07:00", { effortScore: 35 }),
  listActivity("l06", "Morning Miles", "Run", "2026-03-25", "07:00", { effortScore: 28 }),
  listActivity("l07", "Threshold Run", "Run", "2026-03-24", "07:00", { effortScore: 40 }),
  listActivity("l08", "Morning Miles", "Run", "2026-03-23", "07:00", { effortScore: 33 }),
  listActivity("l09", "Weekend Long Run", "Run", "2026-03-22", "08:00", { effortScore: 38, distanceM: 16000, movingTimeS: 5400 }),
  listActivity("l10", "Morning Miles", "Run", "2026-03-21", "07:00", { effortScore: 42 }),
  listActivity("l11", "Morning Miles", "Run", "2026-03-20", "07:00", { effortScore: 25 }),
  listActivity("l12", "Morning Miles", "Run", "2026-03-19", "07:00", { effortScore: 31 }),
  listActivity("l13", "Morning Miles", "Run", "2026-03-18", "07:00", { effortScore: 29 }),
  listActivity("l14", "Morning Miles", "Run", "2026-03-17", "07:00", { effortScore: 36 }),
  listActivity("l15", "Tempo Run", "Run", "2026-03-16", "07:00", { effortScore: 44 }),
  listActivity("l16", "Morning Miles", "Run", "2026-03-15", "07:00", { effortScore: 27 }),
  listActivity("l17", "Morning Miles", "Run", "2026-03-14", "07:00", { effortScore: 32 }),
  listActivity("l18", "Morning Miles", "Run", "2026-03-13", "07:00", { effortScore: 39 }),
  listActivity("l19", "Morning Miles", "Run", "2026-03-12", "07:00", { effortScore: 41 }),
  // The boundary day: three activities, only the first of which fits in page 1
  // (bringing it to exactly 20 rows). The other two arrive on page 2.
  listActivity("l20", "Morning Run", "Run", "2026-03-11", "06:30", { effortScore: 22 }),
];
const mockActivityListPage2 = [
  listActivity("l21", "Midday Ride", "Ride", "2026-03-11", "12:00", { effortScore: 16, distanceM: 12000, movingTimeS: 2400 }),
  listActivity("l22", "Evening Walk", "Walk", "2026-03-11", "19:00", { effortScore: 8, distanceM: 3000, movingTimeS: 1800 }),
];

const mockProfile = {
  goal_type: "general",
  experience_level: "intermediate",
  weekly_days_available: 4,
  current_weekly_km: 40,
  injury_notes: "",
  upcoming_races: [],
  max_hr: 190,
};

// #948: window-navigation support. A synthetic activity every other day over
// ~400 days back from today, so every range/mode/as_of combination the
// stepper can request has real (and DIFFERENT) content to show — the point
// being to prove the arrows actually move the window, not just render it once.
const RANGE_WINDOW_DAYS = { "7D": 7, "30D": 30, "3M": 90, "6M": 180, "1Y": 365 };

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}
function addDaysISO(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return isoDate(d);
}
function todayISO() {
  return isoDate(new Date());
}
function mondayOfISO(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  const day = d.getUTCDay(); // 0=Sun..6=Sat
  const diff = day === 0 ? -6 : 1 - day;
  d.setUTCDate(d.getUTCDate() + diff);
  return isoDate(d);
}
function calendarPeriodStart(rangeKey, asOf) {
  const [y, m] = asOf.split("-").map(Number);
  if (rangeKey === "7D") return mondayOfISO(asOf);
  if (rangeKey === "30D") return `${y}-${String(m).padStart(2, "0")}-01`;
  if (rangeKey === "3M") {
    const qm = Math.floor((m - 1) / 3) * 3 + 1;
    return `${y}-${String(qm).padStart(2, "0")}-01`;
  }
  if (rangeKey === "6M") return m <= 6 ? `${y}-01-01` : `${y}-07-01`;
  return `${y}-01-01`; // 1Y
}
function computeFraming(rangeKey, mode, asOf) {
  const n = RANGE_WINDOW_DAYS[rangeKey] ?? 7;
  if (mode === "calendar") {
    return { period_start: calendarPeriodStart(rangeKey, asOf), period_end: asOf };
  }
  return { period_start: addDaysISO(asOf, -(n - 1)), period_end: asOf };
}

function synthActivities() {
  const today = todayISO();
  const acts = [];
  for (let i = 0; i < 400; i += 2) {
    acts.push({
      date: addDaysISO(today, -i),
      distance_m: 5000 + (i % 5) * 1000,
      moving_time_s: 1800 + (i % 5) * 300,
      effort_score: 40 + (i % 5) * 10,
    });
  }
  return acts; // newest first
}
const SYNTH_ACTIVITIES = synthActivities();
const EARLIEST_ACTIVITY_DATE = SYNTH_ACTIVITIES[SYNTH_ACTIVITIES.length - 1].date;

function activitiesInRange(start, end) {
  return SYNTH_ACTIVITIES.filter((a) => (!start || a.date >= start) && a.date <= end);
}

function buildTrendsResponse(rangeKey, mode, asOf) {
  const { period_start, period_end } = computeFraming(rangeKey, mode, asOf);
  const inWindow = activitiesInRange(period_start, period_end);
  const daily = [];
  for (let d = period_start; d <= period_end; d = addDaysISO(d, 1)) {
    const dayActs = inWindow.filter((a) => a.date === d);
    daily.push({
      date: d,
      total_distance_m: dayActs.reduce((s, a) => s + a.distance_m, 0),
      activity_count: dayActs.length,
    });
  }
  return {
    range: rangeKey,
    summary: {
      total_distance_m: inWindow.reduce((s, a) => s + a.distance_m, 0),
      total_moving_time_s: inWindow.reduce((s, a) => s + a.moving_time_s, 0),
      activity_count: inWindow.length,
      total_suffer_score: inWindow.reduce((s, a) => s + a.effort_score, 0),
    },
    previous_summary: null,
    daily_distance: daily,
    weekly_distance: [],
    daily_time: daily.map((p) => ({
      date: p.date,
      total_moving_time_s: p.activity_count * 1800,
      activity_count: p.activity_count,
    })),
    weekly_time: [],
    daily_suffer_score: daily.map((p) => ({ date: p.date, effort_score: p.activity_count * 40 })),
    weekly_suffer_score: [],
    efficiency_trend: [],
    daily_zone_load: [],
    weekly_zone_load: [],
    biweekly_distance: [],
    monthly_distance: [],
    biweekly_time: [],
    monthly_time: [],
    biweekly_suffer_score: [],
    monthly_suffer_score: [],
    biweekly_zone_load: [],
    monthly_zone_load: [],
  };
}

function buildVolumeResponse(rangeKey, asOf) {
  const days = RANGE_WINDOW_DAYS[rangeKey] ?? 7;
  function framing(mode) {
    const { period_start, period_end } = computeFraming(rangeKey, mode, asOf);
    const inWindow = activitiesInRange(period_start, period_end);
    const distance = inWindow.reduce((s, a) => s + a.distance_m, 0);
    return {
      framing: mode,
      label: mode === "rolling" ? `${days}-day rolling` : "This period",
      window_days: days,
      days_elapsed: days,
      complete: true,
      period_start,
      period_end,
      baseline_start: addDaysISO(period_start, -84),
      baseline_end: addDaysISO(period_start, -1),
      metrics: [
        {
          metric: "sessions", current_all: inWindow.length, current_runs: inWindow.length,
          norm: Math.round(days / 3), norm_recent: Math.round(days / 3), pct_vs_norm: 0,
          direction: "in_line", direction_recent: "in_line",
        },
        {
          metric: "distance_m", current_all: distance, current_runs: distance,
          norm: Math.round(days * 800), norm_recent: Math.round(days * 800), pct_vs_norm: 0,
          direction: "in_line", direction_recent: "in_line",
        },
        {
          metric: "moving_time_s", current_all: 0, current_runs: 0,
          norm: null, norm_recent: null, pct_vs_norm: null,
          direction: "no_norm", direction_recent: "no_norm",
        },
        {
          metric: "effort_score", current_all: 0, current_runs: 0,
          norm: null, norm_recent: null, pct_vs_norm: null,
          direction: "no_norm", direction_recent: "no_norm",
        },
      ],
    };
  }
  return {
    range: rangeKey,
    rolling: framing("rolling"),
    calendar: framing("calendar"),
    has_baseline: true,
    baseline_label: "the last 12 weeks",
  };
}

function buildLoadResponse() {
  const monday = mondayOfISO(todayISO());
  const weeks = [];
  for (let i = 9; i >= 0; i--) {
    const week_start = addDaysISO(monday, -i * 7);
    const score = 150 + ((9 - i) % 4) * 40;
    const hasBaseline = i < 6; // the oldest few weeks abstain (no trailing 4wk yet)
    const status = !hasBaseline ? "no_baseline" : i % 3 === 0 ? "high" : i % 3 === 1 ? "below" : "optimal";
    weeks.push({
      week_start,
      score,
      daily: [20, 30, 0, 40, 0, score - 90, 0].map((v) => Math.max(0, v)),
      target_min: hasBaseline ? Math.round(score * 0.8) : null,
      target_max: hasBaseline ? Math.round(score * 1.3) : null,
      status,
      activities:
        i === 0
          ? [{ id: "42", name: "Morning Tempo", date: week_start, effort_score: 48, headline: "Tempo run" }]
          : [],
    });
  }
  return { weeks, week_starts_on: 0 };
}

// #830: the schedule week. A planned week (so free mode is not the only shape
// exercised) carrying a pinned done session, a floating one whose window has
// narrowed, a suggestion, a rule with a violation, and logged actuals.
const mockScheduleWeek = {
  week_start: "2026-03-23",
  week_end: "2026-03-29",
  is_current_week: true,
  has_plan: true,
  plan_id: "11111111-1111-1111-1111-111111111111",
  headline: {
    planned_running_distance_m: 42000,
    logged_running_distance_m: 18400,
    planned_sessions: 5,
    done_sessions: 2,
  },
  sessions: [
    {
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      window_start: "2026-03-23",
      window_end: "2026-03-23",
      placement: "pinned",
      effective_window_start: "2026-03-23",
      effective_window_end: "2026-03-23",
      has_narrowed: false,
      intent: "easy",
      discipline: "run",
      commitment: "committed",
      status: "done",
      title: "Easy 8k",
      detail: "Conversational the whole way.",
      target_distance_m: 8000,
      planned_distance_m: 8000,
      target_duration_s: null,
      target_effort_score: 40,
      structure: null,
      completed_at: "2026-03-23T08:12:00Z",
      completed_activity_id: "42",
      completion_source: "auto_match",
      dismissed_at: null,
    },
    {
      id: "aaaaaaaa-0000-0000-0000-000000000002",
      window_start: "2026-03-24",
      window_end: "2026-03-24",
      placement: "pinned",
      effective_window_start: "2026-03-24",
      effective_window_end: "2026-03-24",
      has_narrowed: false,
      intent: "strength",
      discipline: "strength",
      commitment: "committed",
      status: "upcoming",
      title: "Lower body",
      detail: "Squat, hinge, calf raises.",
      target_distance_m: null,
      target_duration_s: 2700,
      planned_distance_m: 0,
      target_effort_score: 30,
      structure: null,
      completed_at: null,
      completed_activity_id: null,
      completion_source: null,
      dismissed_at: null,
    },
    {
      id: "aaaaaaaa-0000-0000-0000-000000000003",
      window_start: "2026-03-25",
      window_end: "2026-03-27",
      placement: "window",
      effective_window_start: "2026-03-26",
      effective_window_end: "2026-03-27",
      has_narrowed: true,
      intent: "quality",
      discipline: "run",
      commitment: "committed",
      status: "upcoming",
      title: "6 x 800m",
      detail: "Threshold effort, 90s jog between.",
      target_distance_m: 10000,
      planned_distance_m: 10000,
      target_duration_s: null,
      target_effort_score: 75,
      structure: { reps_planned: 6, rep_distance_m: 800, rest_s: 90 },
      completed_at: null,
      completed_activity_id: null,
      completion_source: null,
      dismissed_at: null,
    },
    {
      id: "aaaaaaaa-0000-0000-0000-000000000004",
      window_start: "2026-03-28",
      window_end: "2026-03-29",
      placement: "window",
      effective_window_start: "2026-03-28",
      effective_window_end: "2026-03-29",
      has_narrowed: false,
      intent: "long",
      discipline: "run",
      commitment: "committed",
      status: "upcoming",
      title: "Long run 18k",
      detail: "Easy throughout, fuel from 60 minutes.",
      target_distance_m: 18000,
      planned_distance_m: 18000,
      target_duration_s: null,
      target_effort_score: 110,
      structure: null,
      completed_at: null,
      completed_activity_id: null,
      completion_source: null,
      dismissed_at: null,
    },
    {
      id: "aaaaaaaa-0000-0000-0000-000000000005",
      window_start: "2026-03-23",
      window_end: "2026-03-29",
      placement: "week",
      effective_window_start: "2026-03-26",
      effective_window_end: "2026-03-29",
      has_narrowed: true,
      intent: "easy",
      discipline: "bike",
      commitment: "suggested",
      status: "upcoming",
      title: "Spin 45 min",
      detail: "Optional aerobic top-up if the legs feel good.",
      target_distance_m: null,
      target_duration_s: 2700,
      planned_distance_m: 0,
      target_effort_score: 25,
      structure: null,
      completed_at: null,
      completed_activity_id: null,
      completion_source: null,
      dismissed_at: null,
    },
  ],
  logged: [
    {
      activity_id: "42",
      local_date: "2026-03-23",
      activity_type: "Run",
      discipline: "run",
      distance_m: 8100,
      moving_time_s: 2580,
      effort_score: 42,
    },
    {
      activity_id: null,
      local_date: "2026-03-25",
      activity_type: "Walk",
      discipline: "walk",
      distance_m: 4200,
      moving_time_s: 3000,
      effort_score: 12,
    },
  ],
  by_discipline: [
    {
      discipline: "run",
      planned_effort_score: 225,
      logged_effort_score: 42,
      planned_sessions: 4,
      logged_sessions: 1,
    },
    {
      discipline: "strength",
      planned_effort_score: 30,
      logged_effort_score: 0,
      planned_sessions: 1,
      logged_sessions: 0,
    },
    {
      discipline: "walk",
      planned_effort_score: 0,
      logged_effort_score: 12,
      planned_sessions: 0,
      logged_sessions: 1,
    },
  ],
  // `statement` is the derived runner-facing rule text the API returns (#844);
  // `label` is the coach's own prose, carried as a subordinate note. The first
  // rule below deliberately keeps the MISLEADING live label that opened #844 —
  // it promises an easy walk against a predicate that forbids one — so the mock
  // exercises the case where the two genuinely diverge.
  rules: [
    {
      kind: "rest_day_after",
      label: "Full rest or easy walk only the day after the long run",
      statement: "Nothing but rest the day after a long session.",
      source: "coach",
      intent: "long",
    },
    {
      kind: "preferred_days",
      label: "Long run needs a free morning — Sat or Sun",
      statement: "Long sessions only on Saturday and Sunday.",
      source: "runner",
      intent: "long",
      weekdays: [5, 6],
    },
  ],
  violations: [
    {
      kind: "no_intent_day_before",
      label: "No heavy legs the day before a quality run",
      statement: "No strength session the day before a quality session.",
      detail: "Lower body sits the day before the 6 x 800m window opens.",
    },
  ],
  norm: [
    {
      metric: "distance_m",
      current_all: 22600,
      current_runs: 18400,
      norm: 38000,
      norm_recent: 36000,
      pct_vs_norm: -12,
      direction: "in_line",
      direction_recent: "in_line",
    },
    {
      metric: "sessions",
      current_all: 2,
      current_runs: 1,
      norm: 5,
      norm_recent: 5,
      pct_vs_norm: -60,
      direction: "down",
      direction_recent: "down",
    },
  ],
  // The runs-only read the free-mode gauge is driven by. Deliberately well
  // below the all-activity `norm` above: that gap is the reason the gauge stopped
  // reading the all-activity figure.
  running_norm: {
    typical_weekly_distance_m: 21000,
    current_distance_m: 18400,
    pct_vs_norm: -12.4,
    direction: "in_line",
    deadband_pct: 15,
  },
};

// #830/#842: the horizon. Twelve weeks carrying every shape the chart has to
// survive — planned weeks with real mixes, sketched weeks, a phase change, an
// EMPTY week the plan says nothing about but still falls inside its own span
// (null load, empty mixes — the divide-by-zero case), a race, a peak that is
// not the first or last week, and a tail of BEYOND_PLAN weeks past the plan's
// own reach — the coach never sketched them, so like the empty week they carry
// null load and no phase, but they read as a different thing entirely (past
// the plan's end, not a gap inside it) and the chart draws the "Plan ends
// here" boundary right before the first one.
const mockScheduleHorizon = {
  weeks: [
    {
      week_start: "2026-03-23",
      phase: "Base",
      planned: true,
      coverage: "planned",
      is_current: true,
      running_distance_m: 42000,
      effort_score: 210,
      discipline_mix: { run: 0.72, strength: 0.18, bike: 0.1 },
      intent_mix: { easy: 0.55, long: 0.3, quality: 0.15 },
    },
    {
      week_start: "2026-03-30",
      phase: "Base",
      planned: true,
      coverage: "planned",
      is_current: false,
      running_distance_m: 46000,
      effort_score: 232,
      discipline_mix: { run: 0.7, strength: 0.2, bike: 0.1 },
      intent_mix: { easy: 0.5, long: 0.32, quality: 0.18 },
    },
    {
      week_start: "2026-04-06",
      phase: "Base",
      planned: true,
      coverage: "planned",
      is_current: false,
      running_distance_m: 34000,
      effort_score: 168,
      discipline_mix: { run: 0.64, strength: 0.24, walk: 0.12 },
      intent_mix: { easy: 0.7, long: 0.3 },
    },
    {
      week_start: "2026-04-13",
      phase: "Build",
      planned: false,
      coverage: "sketched",
      is_current: false,
      running_distance_m: 52000,
      effort_score: 268,
      discipline_mix: { run: 0.75, strength: 0.15, bike: 0.1 },
      intent_mix: { easy: 0.45, long: 0.3, quality: 0.25 },
    },
    {
      week_start: "2026-04-20",
      phase: "Build",
      planned: false,
      coverage: "sketched",
      is_current: false,
      running_distance_m: 56000,
      effort_score: 290,
      discipline_mix: { run: 0.78, strength: 0.14, row: 0.08 },
      intent_mix: { easy: 0.42, long: 0.3, quality: 0.28 },
    },
    // The plan says nothing about this week, but its own span still reaches
    // past it (a later week is sketched) — a genuine interior GAP, distinct
    // from a week past the plan's end. It still arrives so the run of weeks
    // stays continuous and a gap reads as a gap.
    {
      week_start: "2026-04-27",
      phase: null,
      planned: false,
      coverage: "empty",
      is_current: false,
      running_distance_m: null,
      effort_score: null,
      discipline_mix: {},
      intent_mix: {},
    },
    {
      week_start: "2026-05-04",
      phase: "Build",
      planned: false,
      coverage: "sketched",
      is_current: false,
      running_distance_m: 60000,
      effort_score: 312,
      discipline_mix: { run: 0.8, strength: 0.12, bike: 0.08 },
      intent_mix: { easy: 0.4, long: 0.3, quality: 0.3 },
    },
    {
      week_start: "2026-05-11",
      phase: "Build",
      planned: false,
      coverage: "sketched",
      is_current: false,
      running_distance_m: 44000,
      effort_score: 226,
      discipline_mix: { run: 0.7, strength: 0.2, walk: 0.1 },
      intent_mix: { easy: 0.62, long: 0.28, quality: 0.1 },
    },
    {
      week_start: "2026-05-18",
      phase: "Peak",
      planned: false,
      coverage: "sketched",
      is_current: false,
      running_distance_m: 66000,
      effort_score: 340,
      discipline_mix: { run: 0.84, strength: 0.1, other: 0.06 },
      intent_mix: { easy: 0.38, long: 0.3, quality: 0.32 },
    },
    {
      week_start: "2026-05-25",
      phase: "Peak",
      planned: false,
      coverage: "sketched",
      is_current: false,
      running_distance_m: 62000,
      effort_score: 318,
      discipline_mix: { run: 0.82, strength: 0.12, bike: 0.06 },
      intent_mix: { easy: 0.4, long: 0.3, quality: 0.3 },
    },
    // The plan's own reach ends after the Peak week above — the coach never
    // sketched a Taper, so these two are BEYOND_PLAN rather than sketched:
    // null load and no phase, same as the empty week, but the chart must not
    // draw them with the same hollow "shape only" tick, and the last two weeks
    // is exactly the "make the LAST few weeks beyond_plan" case the #842 fix
    // exists to render honestly.
    {
      week_start: "2026-06-01",
      phase: null,
      planned: false,
      coverage: "beyond_plan",
      is_current: false,
      running_distance_m: null,
      effort_score: null,
      discipline_mix: {},
      intent_mix: {},
    },
    {
      week_start: "2026-06-08",
      phase: null,
      planned: false,
      coverage: "beyond_plan",
      is_current: false,
      running_distance_m: null,
      effort_score: null,
      discipline_mix: {},
      intent_mix: {},
    },
  ],
  // A race inside the FIRST few weeks as well as the A race at the end, so the
  // marker is exercised at every range the control offers — a 1M window that
  // dropped every race would never draw one.
  races: [
    {
      id: "33333333-3333-3333-3333-333333333333",
      name: "Spring 10K",
      race_date: "2026-04-11",
      distance_m: 10000,
      priority: "B",
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      name: "Amsterdam Half",
      race_date: "2026-06-13",
      // The exact half-marathon distance the capture UI stores.
      distance_m: 21097.5,
      priority: "A",
    },
  ],
  has_plan: true,
  peak_effort_score: 340,
};

// #830: the runner's goal races. Mutable, because the panel POSTs and DELETEs
// against it and the smoke drives both — a create the server forgets would let a
// broken write pass.
const mockGoalRaces = [
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Amsterdam Half",
    race_date: "2026-06-13",
    distance_m: 21097.5,
    priority: "A",
  },
];

const mockScheduleDraft = {
  status: "active",
  plan_id: "11111111-1111-1111-1111-111111111111",
  generated_at: "2026-03-22T18:00:00Z",
  message: "Your plan is ready.",
};

// #857: the way back to a plan the coach replaced. Two plan ids that trade
// places, so the smoke can prove the round trip -- restore, and the plan just
// stepped away from becomes the one on offer -- rather than only a status code.
const PLAN_A = "11111111-1111-1111-1111-111111111111";
const PLAN_B = "22222222-2222-2222-2222-222222222222";
const mockPlans = { active: PLAN_B, previous: PLAN_A };

function previousPlanPayload() {
  return {
    plan_id: mockPlans.previous,
    superseded_at: "2026-03-22T18:00:00Z",
    generated_at: "2026-03-01T09:00:00Z",
    horizon_end: "2026-12-31",
    sessions_ahead: 14,
    restorable: true,
    message: "Your previous plan is still here.",
  };
}

// #944: a coach report that OFFERS a schedule change. The card's token is minted
// per read on the real server, so the mock mints one too — and burns it on
// confirm, which is what lets the smoke prove the single-use property rather
// than only that a button exists.
const OFFER_TOKEN = "smoke-offer-token-do-not-use-in-prod";
const mockOfferTokens = new Set();

function mockCoachReport() {
  mockOfferTokens.add(OFFER_TOKEN);
  return {
    id: "33333333-3333-3333-3333-333333333333",
    activity_id: "42",
    report: {
      message:
        "Strong tempo. You held the effort right through, and the drift stayed flat.\n\nThursday is where this catches up with you: 12 km of intervals on top of this week is more than the block needs. Take it down to 8 km and keep the shape.",
      headline: "Tempo held, Thursday is too long",
      next_steps: [],
      risks: [],
      questions: [],
      tail_degraded: false,
      // The DURABLE half is deliberately absent from the wire: the server keeps
      // it and sends only the minted card below (#944).
      offer: null,
    },
    meta: {
      confidence: "high",
      model_id: "smoke-model",
      prompt_id: "coach_message_lean_grouped_v9",
      schema_version: "2.0",
      input_hash: "smoke",
      generated_at: "2026-03-28T08:30:00Z",
      policy_violations: [],
      tail_degraded: false,
      voice_stale: false,
    },
    debug: { context_pack: {}, system_prompt: "smoke", raw_llm_response: null },
    created_at: "2026-03-28T08:30:00Z",
    offer: {
      action_type: "adjust_session",
      token: OFFER_TOKEN,
      description: "Change \u201cThursday intervals\u201d (Thu 2 Apr) to 8 km",
      confirm_label: "Change it",
      dismiss_label: "Leave it",
    },
  };
}

const routesToCheck = [
  { path: "/", expectedText: "Weekly Summary" },
  { path: "/activities", expectedText: "All Activities" },
  { path: "/trends", expectedText: "Track your progress over time." },
  // Client-fetched (loading gate), so the server-rendered shell shows the
  // loading state rather than the fetched heading — this still proves the
  // route boots and the mock's /api/trends/load shape is reachable.
  { path: "/load", expectedText: "Loading…" },
  { path: "/profile", expectedText: "Loading profile..." },
  { path: "/activity/42", expectedText: "Morning Tempo" },
  { path: "/schedule", expectedText: "The week ahead" },
  // #830: the horizon is the page's second view. The week stays the default, so
  // what the server renders is the TAB — checking for it is what catches the
  // view being dropped from the page.
  { path: "/schedule", expectedText: "Next 3 months" },
  // The goal-race panel belongs to the whole schedule, so it is server-rendered
  // above the tabs in both views. Its races arrive client-side; the heading is
  // what proves the surface is there at all.
  { path: "/schedule", expectedText: "Goal race" },
];

function createMockApiServer() {
  return http.createServer((req, res) => {
    if (!req.url) {
      res.writeHead(400);
      res.end("Missing request URL");
      return;
    }

    const url = new URL(req.url, MOCK_API_BASE_URL);
    const { pathname, searchParams } = url;

    if (pathname === "/api/activities" && searchParams.get("limit") === "10") {
      return sendJson(res, 200, [mockActivity]);
    }

    // The All Activities view (#240) pages with skip/limit. #947: page 1 is a
    // full 20-row page ending mid-day, page 2 completes that day and then the
    // "Load more" terminator (empty) is exercised on a third fetch.
    if (pathname === "/api/activities" && searchParams.has("skip")) {
      const skip = Number(searchParams.get("skip") ?? "0");
      if (skip === 0) return sendJson(res, 200, mockActivityListPage);
      if (skip === mockActivityListPage.length) return sendJson(res, 200, mockActivityListPage2);
      return sendJson(res, 200, []);
    }

    if (pathname === "/api/activities/42") {
      return sendJson(res, 200, mockActivity);
    }

    // #944: the report, carrying an offer. Its confirm goes to the same endpoint
    // the chat card confirms through, so the mock answers that one too.
    if (pathname === "/api/activities/42/coach-report") {
      return sendJson(res, 200, mockCoachReport());
    }

    if (
      pathname === "/api/coach/threads/actions/confirm" &&
      req.method === "POST"
    ) {
      let body = "";
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        let parsed;
        try {
          parsed = JSON.parse(body || "{}");
        } catch {
          return sendJson(res, 422, { detail: "Body was not JSON" });
        }
        // Single-use, the way the real token store is single-use: the token is
        // spent on the first confirm and a replay is a 404.
        if (!mockOfferTokens.delete(parsed.token)) {
          return sendJson(res, 404, { detail: "Proposed action not found" });
        }
        return sendJson(res, 200, { action_type: "adjust_session" });
      });
      return;
    }

    // #948: the window-navigation floor, fetched once by the Activities and
    // Trends pages.
    if (pathname === "/api/activities/earliest-date") {
      return sendJson(res, 200, { earliest_activity_date: EARLIEST_ACTIVITY_DATE });
    }

    if (pathname === "/api/profile") {
      return sendJson(res, 200, mockProfile);
    }

    // #948: as_of (defaulting to today) drives the whole window, so the
    // stepper's back/forward taps show materially different content.
    if (pathname === "/api/trends") {
      const rangeKey = searchParams.get("range") ?? "30D";
      const mode = searchParams.get("mode") ?? "rolling";
      const asOf = searchParams.get("as_of") ?? todayISO();
      return sendJson(res, 200, buildTrendsResponse(rangeKey, mode, asOf));
    }

    if (pathname === "/api/trends/types") {
      return sendJson(res, 200, ["Run"]);
    }

    if (pathname === "/api/trends/volume") {
      const rangeKey = searchParams.get("range") ?? "7D";
      const asOf = searchParams.get("as_of") ?? todayISO();
      return sendJson(res, 200, buildVolumeResponse(rangeKey, asOf));
    }

    if (pathname === "/api/trends/load") {
      return sendJson(res, 200, buildLoadResponse());
    }

    if (pathname === "/api/schedule/week") {
      return sendJson(res, 200, mockScheduleWeek);
    }

    // #830: the horizon. `weeks` is honoured so the 1M/2M/3M range control is
    // exercised against a server that actually narrows the window.
    if (pathname === "/api/schedule/horizon") {
      const count = Number(searchParams.get("weeks") ?? "12");
      const weeks = mockScheduleHorizon.weeks.slice(0, count);
      // The peak and the race list are scoped to the WINDOW, as the real
      // builder scopes them — a narrower range must not keep scaling its bars
      // against a peak week it no longer shows.
      const loads = weeks.map((w) => w.effort_score).filter(Boolean);
      const lastWeek = weeks[weeks.length - 1];
      const spanEnd = lastWeek
        ? new Date(new Date(`${lastWeek.week_start}T00:00:00Z`).getTime() + 6 * 86400000)
            .toISOString()
            .slice(0, 10)
        : "";
      return sendJson(res, 200, {
        ...mockScheduleHorizon,
        weeks,
        races: mockScheduleHorizon.races.filter((r) => r.race_date <= spanEnd),
        peak_effort_score: loads.length ? Math.max(...loads) : null,
      });
    }

    // The empty-state CTA polls this on mount to pick up an in-flight draft, so
    // it must answer even when the week it renders is not empty.
    if (pathname === "/api/schedule/draft") {
      return sendJson(res, 200, mockScheduleDraft);
    }

    // #857: the previous-plan offer and the restore. The restore answers 204
    // and SWAPS which plan is on offer, so the mock holds the property the real
    // endpoint holds: going back is itself something you can go back from.
    if (pathname === "/api/schedule/plans/previous") {
      return sendJson(res, 200, previousPlanPayload());
    }

    if (
      pathname.startsWith("/api/schedule/plans/") &&
      pathname.endsWith("/restore") &&
      req.method === "POST"
    ) {
      const id = pathname.slice("/api/schedule/plans/".length, -"/restore".length);
      if (id !== mockPlans.previous) {
        return sendJson(res, 422, {
          detail: "That plan is not one you can go back to.",
        });
      }
      mockPlans.previous = mockPlans.active;
      mockPlans.active = id;
      res.writeHead(204);
      return res.end();
    }

    // #830: the goal-race capture surface. The list is the panel's read; POST
    // and DELETE are the runner's two writes, and both mutate the list so the
    // smoke can prove the round trip rather than just the status code.
    if (pathname === "/api/schedule/races" && req.method === "GET") {
      return sendJson(res, 200, mockGoalRaces);
    }

    if (pathname === "/api/schedule/races" && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        let parsed;
        try {
          parsed = JSON.parse(body || "{}");
        } catch {
          return sendJson(res, 422, { detail: "Body was not JSON" });
        }
        if (!parsed.name || !parsed.race_date || !(parsed.distance_m > 0)) {
          return sendJson(res, 422, { detail: "Incomplete race" });
        }
        const race = {
          id: `44444444-4444-4444-4444-${String(mockGoalRaces.length).padStart(12, "0")}`,
          name: parsed.name,
          race_date: parsed.race_date,
          distance_m: parsed.distance_m,
          priority: parsed.priority ?? "A",
        };
        mockGoalRaces.push(race);
        mockGoalRaces.sort((a, b) => (a.race_date < b.race_date ? -1 : 1));
        return sendJson(res, 201, race);
      });
      return;
    }

    if (pathname.startsWith("/api/schedule/races/") && req.method === "DELETE") {
      const id = pathname.slice("/api/schedule/races/".length);
      const index = mockGoalRaces.findIndex((r) => r.id === id);
      if (index === -1) {
        return sendJson(res, 404, { detail: "No such race" });
      }
      mockGoalRaces.splice(index, 1);
      res.writeHead(204);
      res.end();
      return;
    }

    if (pathname === "/api/auth/strava/login") {
      res.writeHead(302, { Location: "https://www.strava.com/oauth/mock" });
      res.end();
      return;
    }

    if (pathname === "/api/auth/strava/status") {
      return sendJson(res, 200, {
        connected: true,
        athlete_id: 12345,
        scope: "read,activity:read_all,profile:read_all",
        expires_at: 9999999999,
      });
    }

    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ detail: `Unhandled smoke route: ${pathname}` }));
  });
}

function sendJson(res, statusCode, body) {
  res.writeHead(statusCode, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function spawnProcess(command, args, options = {}) {
  return spawn(command, args, {
    stdio: "inherit",
    ...options,
  });
}

async function runCommand(command, args, options = {}) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      ...options,
    });

    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} ${args.join(" ")} exited with code ${code}`));
    });
  });
}

async function waitForHttp(url, timeoutMs) {
  const startedAt = Date.now();
  let lastError;

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
      lastError = new Error(`HTTP ${response.status} from ${url}`);
    } catch (error) {
      lastError = error;
    }

    await sleep(500);
  }

  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

async function fetchHtml(pathname) {
  const response = await fetch(`${FRONTEND_BASE_URL}${pathname}`);
  const html = await response.text();
  if (!response.ok) {
    throw new Error(`Expected 200 for ${pathname}, received ${response.status}`);
  }
  return html;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const mockApiServer = createMockApiServer();
const runningChildren = [];

function cleanup() {
  for (const child of runningChildren) {
    if (!child.killed) {
      child.kill("SIGTERM");
    }
  }
  mockApiServer.close();
}

process.on("SIGINT", () => {
  cleanup();
  process.exit(1);
});

process.on("SIGTERM", () => {
  cleanup();
  process.exit(1);
});

process.on("exit", cleanup);

async function main() {
  await new Promise((resolve, reject) => {
    mockApiServer.once("error", reject);
    mockApiServer.listen(MOCK_API_PORT, "127.0.0.1", resolve);
  });

  // The smoke runs against a mock backend with no auth, so Clerk must be off:
  // an empty publishable key makes the middleware/layout pass-through (CI has no
  // .env.local, but a dev machine does, and Next would otherwise inline its key
  // into the build and gate every route behind a sign-in redirect).
  //
  // BACKEND_URL must point at the mock too: server components resolve their base
  // URL as BACKEND_URL || NEXT_PUBLIC_API_BASE_URL || 127.0.0.1:8000 (lib/api.ts),
  // and a dev machine's .env.local sets BACKEND_URL=http://localhost:8000. Without
  // overriding it, server-side fetches escape to the real, Clerk-gated backend
  // (401) instead of the mock, 500-ing /activity/42 (#538). CI has no .env.local
  // so this is invisible there, but it breaks local `make smoke`.
  const smokeEnv = {
    ...process.env,
    BACKEND_URL: MOCK_API_BASE_URL,
    NEXT_PUBLIC_API_BASE_URL: MOCK_API_BASE_URL,
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "",
    CLERK_SECRET_KEY: "",
  };

  await runCommand("npm", ["run", "build"], { env: smokeEnv });

  const nextServer = spawnProcess(
    "npm",
    ["run", "start", "--", "--hostname", "127.0.0.1", "--port", `${FRONTEND_PORT}`],
    { env: smokeEnv },
  );
  runningChildren.push(nextServer);

  await waitForHttp(FRONTEND_BASE_URL, 30000);

  for (const route of routesToCheck) {
    const html = await fetchHtml(route.path);
    if (!html.includes(route.expectedText)) {
      throw new Error(
        `Route ${route.path} did not include expected text: ${route.expectedText}`,
      );
    }
  }

  // #830: the horizon fetches client-side, so no server-rendered route touches
  // it. Drive the endpoint through the app's own proxy instead, which exercises
  // the same path the browser takes and proves the range control's `weeks`
  // parameter reaches the backend rather than being ignored.
  const horizonResponse = await fetch(`${FRONTEND_BASE_URL}/api/schedule/horizon?weeks=4`);
  if (!horizonResponse.ok) {
    throw new Error(`Expected 200 for /api/schedule/horizon, received ${horizonResponse.status}`);
  }
  const horizon = await horizonResponse.json();
  if (!Array.isArray(horizon.weeks) || horizon.weeks.length !== 4) {
    throw new Error(
      `/api/schedule/horizon?weeks=4 returned ${horizon.weeks?.length} weeks, expected 4`,
    );
  }
  if (typeof horizon.peak_effort_score !== "number" || horizon.peak_effort_score <= 0) {
    throw new Error("/api/schedule/horizon returned no peak to scale the bars against");
  }
  // The race marker is drawn from this list, so a horizon that returns no race
  // inside a narrow window would silently lose it.
  if (!Array.isArray(horizon.races) || horizon.races.length === 0) {
    throw new Error("/api/schedule/horizon?weeks=4 returned no race to mark");
  }

  // #830: goal-race capture. Also client-side only, so the same proxy path is
  // driven directly — list, create, delete — which is the exact sequence the
  // panel performs and the one that must survive the metres conversion.
  const listResponse = await fetch(`${FRONTEND_BASE_URL}/api/schedule/races`);
  if (!listResponse.ok) {
    throw new Error(`Expected 200 for /api/schedule/races, received ${listResponse.status}`);
  }
  const races = await listResponse.json();
  if (!Array.isArray(races) || races.length !== 1) {
    throw new Error(`/api/schedule/races returned ${races?.length} races, expected 1`);
  }

  const createResponse = await fetch(`${FRONTEND_BASE_URL}/api/schedule/races`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // The exact half-marathon distance: the UI converts km to metres, and 21.1
    // km stored as 21000 would be a plan built for the wrong race.
    body: JSON.stringify({
      name: "Rotterdam Half",
      race_date: "2026-05-17",
      distance_m: 21097.5,
      priority: "B",
    }),
  });
  if (createResponse.status !== 201) {
    throw new Error(
      `Expected 201 from POST /api/schedule/races, received ${createResponse.status}`,
    );
  }
  const created = await createResponse.json();
  if (created.distance_m !== 21097.5) {
    throw new Error(
      `POST /api/schedule/races stored ${created.distance_m} m, expected the exact 21097.5`,
    );
  }

  const deleteResponse = await fetch(
    `${FRONTEND_BASE_URL}/api/schedule/races/${created.id}`,
    { method: "DELETE" },
  );
  if (deleteResponse.status !== 204) {
    throw new Error(
      `Expected 204 from DELETE /api/schedule/races/{id}, received ${deleteResponse.status}`,
    );
  }

  const afterResponse = await fetch(`${FRONTEND_BASE_URL}/api/schedule/races`);
  const after = await afterResponse.json();
  if (!Array.isArray(after) || after.length !== 1) {
    throw new Error(
      `/api/schedule/races returned ${after?.length} races after the delete, expected 1`,
    );
  }

  // #857: the restore round trip through the proxy. Client-side only, like the
  // races above, so the same path is driven directly.
  const previousResponse = await fetch(
    `${FRONTEND_BASE_URL}/api/schedule/plans/previous`,
  );
  if (!previousResponse.ok) {
    throw new Error(
      `Expected 200 for /api/schedule/plans/previous, received ${previousResponse.status}`,
    );
  }
  const previous = await previousResponse.json();
  if (!previous.plan_id || previous.restorable !== true) {
    throw new Error(
      `/api/schedule/plans/previous returned nothing to go back to: ${JSON.stringify(previous)}`,
    );
  }

  const restoreResponse = await fetch(
    `${FRONTEND_BASE_URL}/api/schedule/plans/${previous.plan_id}/restore`,
    { method: "POST" },
  );
  if (restoreResponse.status !== 204) {
    throw new Error(
      `Expected 204 from POST /api/schedule/plans/{id}/restore, received ${restoreResponse.status}`,
    );
  }

  const swapped = await (
    await fetch(`${FRONTEND_BASE_URL}/api/schedule/plans/previous`)
  ).json();
  if (swapped.plan_id === previous.plan_id) {
    throw new Error(
      "restoring left the same plan on offer: going back must itself be reversible",
    );
  }

  // #944: the report's offer round trip, through the app's own proxy — the same
  // path the card's confirm button takes. The report is fetched client-side, so
  // no server-rendered route sees the card; this proves the endpoint behind it,
  // and the browser pass proves the card itself.
  const reportResponse = await fetch(
    `${FRONTEND_BASE_URL}/api/activities/42/coach-report`,
  );
  if (!reportResponse.ok) {
    throw new Error(
      `Expected 200 for /api/activities/42/coach-report, received ${reportResponse.status}`,
    );
  }
  const reportBody = await reportResponse.json();
  if (!reportBody.offer?.token || !reportBody.offer?.confirm_label) {
    throw new Error("the coach report carried no tappable offer");
  }
  if (reportBody.report?.offer) {
    throw new Error(
      "the stored offer rode the response; only the minted card should",
    );
  }

  const confirmResponse = await fetch(
    `${FRONTEND_BASE_URL}/api/coach/threads/actions/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: reportBody.offer.token }),
    },
  );
  if (confirmResponse.status !== 200) {
    throw new Error(
      `Expected 200 from the offer confirm, received ${confirmResponse.status}`,
    );
  }

  // Single-use: the same token again is spent.
  const replayResponse = await fetch(
    `${FRONTEND_BASE_URL}/api/coach/threads/actions/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: reportBody.offer.token }),
    },
  );
  if (replayResponse.status !== 404) {
    throw new Error(
      `A spent offer token was accepted again (${replayResponse.status}); it must be single-use`,
    );
  }

  console.log("Frontend smoke checks passed.");

  // SMOKE_HOLD=1 keeps the mock backend and the Next server up instead of tearing
  // them down, and prints where they are. The checks above are all server-side or
  // proxy-driven; anything that only exists once React has rendered (the offer
  // card is one) has to be looked at in a real browser, and this is how you get
  // a running app to point one at.
  if (process.env.SMOKE_HOLD) {
    console.log(`SMOKE_HOLD: frontend at ${FRONTEND_BASE_URL} (mock API ${MOCK_API_BASE_URL})`);
    await new Promise(() => {});
  }
}

main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    cleanup();
    await sleep(250);
  });
