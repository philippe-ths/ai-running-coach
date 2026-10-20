// Smoke harness module (#1103): mock API fixtures and trends builders.

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
      zone_2_plus_minutes: 91.0,
    },
    previous_summary: {
      total_distance_m: 0,
      total_moving_time_s: 0,
      activity_count: 0,
      total_suffer_score: 0,
      zone_2_plus_minutes: 70.0,
    },
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
    daily_zone_load: daily.map((point) => ({
      date: point.date,
      easy_min: point.activity_count * 12,
      moderate_min: point.activity_count * 6,
      hard_min: point.activity_count * 2,
    })),
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
        {
          metric: "zone_2_plus_minutes", current_all: 91, current_runs: 91,
          norm: 75, norm_recent: null, pct_vs_norm: 21.3,
          direction: "up", direction_recent: "no_norm",
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


export {
  EARLIEST_ACTIVITY_DATE,
  addDaysISO,
  buildLoadResponse,
  buildTrendsResponse,
  buildVolumeResponse,
  mockActivity,
  mockActivityListPage,
  mockActivityListPage2,
  mockProfile,
  todayISO,
};
