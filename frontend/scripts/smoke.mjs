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
  rules: [
    {
      kind: "rest_day_after",
      label: "A full rest day after the long run",
      source: "coach",
      intent: "long",
    },
    {
      kind: "preferred_days",
      label: "Long run needs a free morning — Sat or Sun",
      source: "runner",
      intent: "long",
      weekdays: [5, 6],
    },
  ],
  violations: [
    {
      kind: "no_intent_day_before",
      label: "No heavy legs the day before a quality run",
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
};

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

    // The empty-state CTA polls this on mount to pick up an in-flight draft, so
    // it must answer even when the week it renders is not empty.
    if (pathname === "/api/schedule/draft") {
      return sendJson(res, 200, mockScheduleDraft);
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
