import { MOCK_API_PORT, FRONTEND_PORT, MOCK_API_BASE_URL, FRONTEND_BASE_URL } from "./smoke/ports.mjs";
import { createMockApiServer } from "./smoke/mockServer.mjs";
import { spawnProcess, runCommand, waitForHttp, fetchHtml, sleep } from "./smoke/processHelpers.mjs";

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
  // below the tabs in both views. Its races arrive client-side; the heading is
  // what proves the surface is there at all.
  // (The heading has read "Goals" since #1042 made a goal more than a race.)
  { path: "/schedule", expectedText: "Goals" },
  // #1064: the season card sits under the goals, below both views.
  { path: "/schedule", expectedText: "Your season" },
  // #964: the legal pages. These prove the routes BOOT and render their content.
  // They do NOT prove the pages are publicly reachable, which is the property
  // that actually matters for them: the smoke harness runs with no Clerk
  // publishable key, so `clerkEnabled` is false and middleware.ts is a
  // pass-through here. Whether `isPublicRoute` still covers them can only be
  // shown with Clerk ON -- see the #964 PR for that check, and re-run it by hand
  // if the matcher or the public-route list is ever touched.
  { path: "/privacy", expectedText: "Health data, stated plainly" },
  { path: "/terms", expectedText: "This is not medical advice" },
];

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
  //
  // NEXT_PUBLIC_API_BASE_URL stays empty, as in production: client fetches then
  // go to relative /api/* and through the Next proxy to BACKEND_URL. Pointing it
  // at the mock sends a browser cross-origin to a server with no CORS, so under
  // SMOKE_HOLD every client-rendered panel showed its error state (#1076).
  const smokeEnv = {
    ...process.env,
    BACKEND_URL: MOCK_API_BASE_URL,
    NEXT_PUBLIC_API_BASE_URL: "",
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
  // #1064: the challenge line and the plan's shortfalls ride the horizon read.
  const challengeLine = horizon.weeks[1]?.challenges?.[0];
  if (!challengeLine || challengeLine.met !== false) {
    throw new Error("/api/schedule/horizon returned no missed challenge week to mark");
  }
  if (!Array.isArray(horizon.shortfalls) || horizon.shortfalls.length === 0) {
    throw new Error("/api/schedule/horizon returned no shortfalls to state");
  }
  // The race marker is drawn from this list, so a horizon that returns no race
  // inside a narrow window would silently lose it.
  if (!Array.isArray(horizon.races) || horizon.races.length === 0) {
    throw new Error("/api/schedule/horizon?weeks=4 returned no race to mark");
  }

  // #1064: the season is client-fetched too, so the endpoint is driven through
  // the proxy: the shape the card reads, and the 202 the start button expects.
  const seasonResponse = await fetch(`${FRONTEND_BASE_URL}/api/schedule/season`);
  if (!seasonResponse.ok) {
    throw new Error(`Expected 200 for /api/schedule/season, received ${seasonResponse.status}`);
  }
  const season = await seasonResponse.json();
  if (season.status !== "active" || !season.plan?.goals?.length || !season.plan?.phases?.length) {
    throw new Error("/api/schedule/season returned no active season to draw");
  }
  if (!season.challenges?.[0]?.weeks?.length) {
    throw new Error("/api/schedule/season returned a challenge goal with no weeks");
  }
  const seasonStart = await fetch(`${FRONTEND_BASE_URL}/api/schedule/season`, { method: "POST" });
  if (seasonStart.status !== 202) {
    throw new Error(`Expected 202 from POST /api/schedule/season, received ${seasonStart.status}`);
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
