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

const routesToCheck = [
  { path: "/", expectedText: "Weekly Summary" },
  { path: "/activities", expectedText: "All Activities" },
  { path: "/trends", expectedText: "Track your progress over time." },
  { path: "/profile", expectedText: "Loading profile..." },
  { path: "/activity/42", expectedText: "Morning Tempo" },
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

  await runCommand("npm", ["run", "build"], {
    env: {
      ...process.env,
      NEXT_PUBLIC_API_BASE_URL: MOCK_API_BASE_URL,
    },
  });

  const nextServer = spawnProcess(
    "npm",
    ["run", "start", "--", "--hostname", "127.0.0.1", "--port", `${FRONTEND_PORT}`],
    {
      env: {
        ...process.env,
        NEXT_PUBLIC_API_BASE_URL: MOCK_API_BASE_URL,
      },
    },
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
