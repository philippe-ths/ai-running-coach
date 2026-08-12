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
  metrics: null,
  streams: [],
  splits: [],
};

const mockProfile = {
  goal_type: "general",
  experience_level: "intermediate",
  weekly_days_available: 4,
  current_weekly_km: 40,
  injury_notes: "",
  upcoming_races: [],
  max_hr: 190,
};

const mockTrends = {
  summary: {
    total_distance_m: 25000,
    total_moving_time_s: 7200,
    activity_count: 3,
    total_suffer_score: 120,
  },
  previous_summary: {
    total_distance_m: 22000,
    total_moving_time_s: 6900,
    activity_count: 3,
    total_suffer_score: 105,
  },
  daily_distance: [],
  weekly_distance: [],
  daily_time: [],
  weekly_time: [],
  daily_suffer_score: [],
  weekly_suffer_score: [],
  efficiency_trend: [],
  daily_zone_load: [],
  weekly_zone_load: [],
};

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

const routesToCheck = [
  { path: "/", expectedText: "Weekly Summary" },
  { path: "/activities", expectedText: "All Activities" },
  { path: "/trends", expectedText: "Track your progress over time." },
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

    // The All Activities view (#240) pages with skip/limit. Return one page, then
    // empty, so the "Load more" terminator is exercised.
    if (pathname === "/api/activities" && searchParams.has("skip")) {
      const skip = Number(searchParams.get("skip") ?? "0");
      return sendJson(res, 200, skip === 0 ? [mockActivity] : []);
    }

    if (pathname === "/api/activities/42") {
      return sendJson(res, 200, mockActivity);
    }

    if (pathname === "/api/profile") {
      return sendJson(res, 200, mockProfile);
    }

    if (pathname === "/api/trends") {
      return sendJson(res, 200, mockTrends);
    }

    if (pathname === "/api/trends/types") {
      return sendJson(res, 200, ["Run"]);
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

  console.log("Frontend smoke checks passed.");
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
