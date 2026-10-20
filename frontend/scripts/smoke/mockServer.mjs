// Smoke harness module (#1103): the mock API server and its route handlers.
import http from "node:http";
import { MOCK_API_BASE_URL } from "./ports.mjs";
import {
  EARLIEST_ACTIVITY_DATE,
  buildLoadResponse,
  buildTrendsResponse,
  buildVolumeResponse,
  mockActivity,
  mockActivityListPage,
  mockActivityListPage2,
  mockProfile,
  todayISO,
} from "./fixturesCore.mjs";
import {
  buildSeason,
  mockCoachReport,
  mockGoalRaces,
  mockOfferTokens,
  mockPlans,
  mockScheduleDraft,
  mockScheduleHorizon,
  mockScheduleWeek,
  previousPlanPayload,
} from "./fixturesSchedule.mjs";

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

    // #1064: the season. GET is the card's read; POST answers 202 like the real
    // endpoint, which starts planning on the worker.
    if (pathname === "/api/schedule/season") {
      if (req.method === "POST") return sendJson(res, 202, { status: "drafting" });
      return sendJson(res, 200, buildSeason());
    }

    // The empty-state CTA polls this on mount to pick up an in-flight draft, so
    // it must answer even when the week it renders is not empty.
    if (pathname === "/api/schedule/draft") {
      return sendJson(res, 200, mockScheduleDraft);
    }

    // #1092: the schedule polls these on mount. Answered as an idle backend
    // would: no amendment in flight, and every flag at its code default (True).
    if (pathname === "/api/schedule/amendment" && req.method === "GET") {
      return sendJson(res, 200, {
        status: null,
        start: null,
        end: null,
        changes: [],
        detail: null,
      });
    }

    if (pathname === "/api/coach/feature-flags") {
      return sendJson(res, 200, {
        voice: true,
        stance: true,
        user_materials: true,
        sleep_quality: true,
        stops_analysis: true,
        memory: true,
        threads: true,
        schedule: true,
      });
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

export { createMockApiServer, sendJson };
