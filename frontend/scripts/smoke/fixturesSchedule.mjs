// Smoke harness module (#1103): mock schedule, plan and coach-report fixtures.
import {
  addDaysISO,
  todayISO,
} from "./fixturesCore.mjs";

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
      // #1064: the challenge line for a covered week. A past week is judged on
      // what was done (`actual`/`met`), a week ahead on what the plan holds.
      challenges: [
        {
          goal_id: "55555555-5555-5555-5555-555555555555",
          name: "10h a week, zone 2+",
          index: 1,
          weeks: 10,
          metric: "zone_time_s",
          min_zone: 2,
          threshold: 36000,
          planned: 37100,
          actual: 37100,
          met: true,
        },
      ],
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
      // #1064: the challenge line for a covered week. A past week is judged on
      // what was done (`actual`/`met`), a week ahead on what the plan holds.
      challenges: [
        {
          goal_id: "55555555-5555-5555-5555-555555555555",
          name: "10h a week, zone 2+",
          index: 2,
          weeks: 10,
          metric: "zone_time_s",
          min_zone: 2,
          threshold: 36000,
          planned: 36000,
          actual: 33100,
          met: false,
        },
      ],
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
      // #1064: the challenge line for a covered week. A past week is judged on
      // what was done (`actual`/`met`), a week ahead on what the plan holds.
      challenges: [
        {
          goal_id: "55555555-5555-5555-5555-555555555555",
          name: "10h a week, zone 2+",
          index: 3,
          weeks: 10,
          metric: "zone_time_s",
          min_zone: 2,
          threshold: 36000,
          planned: 32400,
          actual: null,
          met: null,
        },
      ],
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
  // #1064: what the plan cannot do, stated rather than left out.
  shortfalls: [
    "Week of 6 Apr: the plan holds 9 h of zone 2+ against the 10 h the challenge needs; the 4 days left cannot carry more.",
  ],
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

// #1064: the season, the coach's read of every goal together. Dates hang off
// today so the card's "week n of N", today marker and ahead/past weeks are
// exercised whatever day the smoke runs.
function buildSeason() {
  const today = todayISO();
  const raceDay = addDaysISO(today, 120);
  const challengeStart = addDaysISO(today, -21);
  const weeks = Array.from({ length: 10 }, (_, i) => ({
    week_start: addDaysISO(challengeStart, i * 7),
    index: i + 1,
    threshold: 36000,
    planned: i === 5 ? 33000 : 37000,
    actual: i < 3 ? (i === 1 ? 31000 : 37500) : null,
    met: i < 3 ? i !== 1 : null,
  }));
  return {
    id: "66666666-6666-6666-6666-666666666666",
    status: "active",
    generated_at: `${today}T08:00:00Z`,
    model_id: "claude-opus-5-5",
    plan: {
      summary:
        "Hold ten hours of zone 2 a week first; it is the harder constraint and the base the half needs anyway. Race the 10K as a rehearsal, not a target.",
      goals: [
        {
          goal_id: "22222222-2222-2222-2222-222222222222",
          kind: "race",
          success: "Run a half marathon PB without the last 5 km fading.",
          date: raceDay,
          window_start: null,
          window_end: null,
          approach: "Two quality days a week from week 6, one of them race-pace work.",
          events: [
            {
              name: "<b>Paddock Wood Half</b> [bold](x)",
              date: raceDay,
              window_start: null,
              window_end: null,
              distance_m: 21097.5,
              url: "https://example.org/paddock-wood-half",
              why: "Flat and local, <i>no</i> travel needed.",
            },
          ],
          challenge: null,
        },
        {
          goal_id: "55555555-5555-5555-5555-555555555555",
          kind: "challenge",
          success: "Ten straight weeks at or above ten hours of zone 2+.",
          date: null,
          window_start: null,
          window_end: null,
          approach: "Walk and ride the hours up; the runs stay easy.",
          events: [],
          challenge: {
            metric: "zone_time_s",
            disciplines: [],
            min_zone: 2,
            at_least: 36000,
            weeks: 10,
            start: challengeStart,
          },
        },
        {
          goal_id: "77777777-7777-7777-7777-777777777777",
          kind: "someday",
          success: "Last man standing at a backyard ultra, when the base is there.",
          date: null,
          window_start: null,
          window_end: null,
          approach: "Not this season.",
          events: [],
          challenge: null,
        },
      ],
      phases: [
        { kind: "base", start: addDaysISO(today, -21), end: addDaysISO(today, 48), goal_id: null, focus: "Hours first", weekly_hours: 10, run_km: 30, long_run_km: 14 },
        { kind: "build", start: addDaysISO(today, 49), end: addDaysISO(today, 98), goal_id: "22222222-2222-2222-2222-222222222222", focus: "Race pace", weekly_hours: 11, run_km: 42, long_run_km: 18 },
        { kind: "taper", start: addDaysISO(today, 99), end: addDaysISO(today, 119), goal_id: "22222222-2222-2222-2222-222222222222", focus: "", weekly_hours: 7, run_km: 25, long_run_km: 10 },
        { kind: "race", start: raceDay, end: raceDay, goal_id: "22222222-2222-2222-2222-222222222222", focus: "", weekly_hours: null, run_km: null, long_run_km: null },
        { kind: "recover", start: addDaysISO(today, 121), end: addDaysISO(today, 134), goal_id: null, focus: "", weekly_hours: 5, run_km: 12, long_run_km: null },
      ],
    },
    goals: [
      { id: "22222222-2222-2222-2222-222222222222", name: "Paddock Wood Half", race_date: raceDay, window_start: null, window_end: null, distance_m: 21097.5, target_time_s: null, notes: null, booked: true, priority: "A" },
      { id: "55555555-5555-5555-5555-555555555555", name: "10h a week, any activity, zone 2+", race_date: null, window_start: null, window_end: null, distance_m: null, target_time_s: null, notes: null, booked: false, priority: "B" },
      { id: "77777777-7777-7777-7777-777777777777", name: "Backyard ultra", race_date: null, window_start: null, window_end: null, distance_m: null, target_time_s: null, notes: null, booked: false, priority: "C" },
    ],
    challenges: [
      {
        goal_id: "55555555-5555-5555-5555-555555555555",
        name: "10h a week, any activity, zone 2+",
        rule: {
          metric: "zone_time_s",
          disciplines: [],
          min_zone: 2,
          at_least: 36000,
          weeks: 10,
          start: challengeStart,
        },
        weeks,
        streak: 1,
      },
    ],
    stale: false,
    regenerating: false,
    message: null,
  };
}

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

export {
  buildSeason,
  mockCoachReport,
  mockGoalRaces,
  mockOfferTokens,
  mockPlans,
  mockScheduleDraft,
  mockScheduleHorizon,
  mockScheduleWeek,
  previousPlanPayload,
};
