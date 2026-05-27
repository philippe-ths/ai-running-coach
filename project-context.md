## Product Summary

Running Coach is a local-first MVP that connects to Strava, ingests running activities, computes training signals, and produces opinionated post-run analysis.
The intended user is an individual runner running the app on their own machine against their own Strava account.
The core flow is: connect Strava → sync activities → deep-process a run → view derived metrics and an LLM-generated coach report on the activity page.

## Domain Concepts

A `User` owns a `UserProfile` (goal, experience, weekly volume, max HR, races, injuries) and a linked `StravaAccount` (OAuth tokens, athlete id).
An `Activity` is a single Strava activity record owned by a `User`, identified by `strava_activity_id`.
An `ActivityStream` holds per-sample time-series data (HR, pace, cadence, power) attached to an `Activity`.
A `DerivedMetric` row attaches one set of computed signals to one `Activity` (activity class, effort score, pace variability, HR drift, time-in-zones, stops, efficiency, intervals, flags, confidence, risk).
A `CheckIn` captures subjective post-run input from the user against an `Activity`.
A `CoachReport` is the cached structured LLM analysis for an `Activity`; `CoachChatMessage` rows form a follow-up conversation against that activity.
A `RunnerBaseline` stores rolling baselines used for comparison and drift detection.

## Scope

The backend exposes JSON endpoints under `/api` for health, Strava OAuth, profile CRUD, activity listing/detail, sync, deep processing, intent labelling, check-ins, trends, coach report, and coach chat.
Strava ingestion supports both manual sync (`POST /api/sync`) and incoming webhooks (`/api/webhooks/strava`); webhook events enqueue RQ jobs that fetch and persist activities.
Deep processing classifies the activity, computes metrics from streams (smoothing, splits, intervals, stops, efficiency, risk), and runs workout matching against any planned workout.
The coach pipeline builds a context pack, calls Anthropic, validates the response against a Pydantic schema, then runs a deterministic policy validator before storing the report.
The frontend renders an activity list on the home page, a per-activity detail page with charts and panels, a profile page, and a trends page with filters and chart views.
Planned-workout capture is not yet implemented; `_extract_planned_workout` in `services/processing/engine.py` returns `None` as a placeholder.
There is no multi-user auth layer; the backend assumes a single local user and auto-creates one on first profile read.
There is no production deployment target in-repo; the only runtime is local via `docker compose` plus uvicorn and `next dev`.

## Important Constraints

Settings come from `backend/.env` via `pydantic-settings`; `DATABASE_URL` is required and the app will not boot without it.
Anthropic access requires `ANTHROPIC_API_KEY`; the coach model id and prompt id are configured via `COACH_MODEL_ID` and `COACH_PROMPT_ID`.
CORS is restricted to `http://localhost:3000` and `http://localhost:8000` in `app/main.py`.
The deterministic policy validator in `services/coach/validator.py` rejects LLM output that claims specific interval execution counts (e.g. "8x400m", "executed 8") and other policy violations; per `ai-workflow.md` this gate must not be bypassed.
When data confidence is low, downstream analysis is expected to default to conservative output (documented intent in `README.md`).
Postgres is exposed on host port `5433` (mapped from container `5432`); Redis on `6379`; backend on `8000`; frontend on `3000`.
Backend test baseline excludes tests marked `integration` (pytest marker registered in `pyproject.toml`).

## Architecture Summary

The backend is a FastAPI app (`app/main.py`) wiring routers from `app/api/*` under the `/api` prefix.
Persistence uses SQLAlchemy 2.x ORM with a Postgres database; schema migrations live under `backend/alembic/versions/`.
Background work runs via Redis-backed RQ; jobs are defined in `app/jobs/` and a worker is started with `rq worker`.
The Strava integration lives in `app/services/strava/client.py` and is orchestrated by `app/services/activity_service.py`.
Processing is a pipeline of pure-ish functions in `app/services/processing/` (smoothing, metrics, classifier, splits, intervals, stops, flags, risk, workout matching) composed by `engine.py`.
The coach layer is `context.py` (builds the pack) → `llm.py` (Anthropic client) → Pydantic schemas in `app/schemas/coach.py` → `validator.py` (policy gate) → `service.py` (caches result in `CoachReport`).
The frontend is a Next.js 14 App Router project; pages live under `frontend/app/`, reusable UI under `frontend/components/`, and the typed API client and shared types under `frontend/lib/`.
Data flow: Strava API → activity_service → Activity/ActivityStream rows → processing engine → DerivedMetric → coach service → CoachReport → frontend via `/api/activities/{id}` and `/api/activities/{id}/coach-report`.

## Key Dependencies

`fastapi`, `uvicorn`: HTTP server and ASGI runtime for the backend.
`sqlalchemy`, `psycopg`, `alembic`: ORM, Postgres driver, and schema migrations.
`pydantic`, `pydantic-settings`: request/response schemas and environment configuration.
`httpx`: outbound HTTP client used for Strava and Anthropic calls.
`redis`, `rq`: job queue for sync and processing background work.
`numpy`: numerical computation in the processing pipeline (smoothing, metrics, intervals).
`anthropic`: Claude API client used by the coach service.
`python-multipart`: form parsing required by FastAPI for non-JSON request bodies.
`pytest`, `pytest-asyncio` (test extra): test runner and async test support.
`next`, `react`, `react-dom`: frontend framework and renderer.
`recharts`: charting library for stream and trend views.
`react-markdown`: renders the coach report body.
`date-fns`: date formatting in activity and trends views.
`lucide-react`: icon set used across the UI.
`tailwindcss`, `@tailwindcss/typography`, `autoprefixer`, `postcss`: styling pipeline.
`typescript`, `eslint`, `eslint-config-next`: type checking and lint baseline.

## Project Structure

`backend/app/main.py` boots the FastAPI app and registers routers.
`backend/app/api/` contains one router per resource: `health.py`, `auth.py`, `profile.py`, `activities.py`, `webhooks.py`, `trends.py`, `coach.py`.
`backend/app/core/config.py` defines the typed settings object; `backend/app/core/queue.py` holds RQ queue setup.
`backend/app/db/base.py` defines the SQLAlchemy `Base`; `backend/app/db/session.py` provides `SessionLocal` and the `get_db` dependency.
`backend/app/models/` holds one ORM model per file (`activity`, `activity_stream`, `checkin`, `coach_chat_message`, `coach_report`, `derived_metric`, `runner_baseline`, `strava_account`, `user`, `user_profile`) with a barrel `__init__.py`.
`backend/app/schemas/` holds Pydantic request/response schemas, one file per domain (`activity`, `chat`, `checkin`, `coach`, `detail`, `profile`, `sync`, `trends`, `user`).
`backend/app/services/activity_service.py` owns Strava-to-DB ingestion and stream fetching.
`backend/app/services/strava/client.py` is the Strava HTTP client.
`backend/app/services/processing/` is the metrics pipeline (`engine.py` orchestrates `smoothing.py`, `metrics.py`, `classifier.py`, `splits.py`, `intervals.py`, `stops.py`, `flags.py`, `risk.py`, `workout_matching.py`).
`backend/app/services/coach/` owns the LLM coach (`context.py`, `prompts.py`, `llm.py`, `validator.py`, `service.py`, `chat.py`).
`backend/app/services/trends.py` produces aggregated trend data; `backend/app/services/units/cadence.py` normalises cadence units.
`backend/app/jobs/strava_sync.py` defines RQ job entry points; `backend/app/worker.py` is the worker bootstrap.
`backend/alembic/versions/` holds migrations, one per schema change.
`backend/tests/` holds pytest suites covering analysis, intervals, policy validator, sync integration, webhooks, stream metrics, workout matching, smoke, and others.
`frontend/app/page.tsx` is the home view; `frontend/app/activity/[id]/page.tsx` is the activity detail view.
`frontend/app/profile/page.tsx` is the profile editor; `frontend/app/trends/page.tsx` hosts the trends dashboard.
`frontend/components/` holds activity panels (advanced metrics, splits, stops, efficiency, stream charts, coach report, coach chat, intent selector, check-in form) and a `trends/` subfolder with chart and filter components.
`frontend/lib/api.ts` is the `fetchFromAPI` helper that points at `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`).
`frontend/lib/format.ts` exports the `formatPace`, `formatDuration`, `formatDistanceKm` helpers.
`frontend/lib/types/` holds domain types (`activity`, `chat`, `coach`, `metrics`, `profile`, `trends`); `frontend/lib/types.ts` is the barrel.
`frontend/scripts/smoke.mjs` is the readiness smoke harness that boots a mock API and Next dev server.
`docker-compose.yml` defines Postgres 16 and Redis 7 services for local development.
`Makefile` exposes `smoke`, `backend-smoke`, `frontend-smoke`, `test`, `backend-test`, `frontend-test`.

## Testing Overview

Backend tests run via `python -m pytest`; the global baseline command is `make backend-test`, which excludes tests marked `integration`.
Backend smoke is a single file (`backend/tests/test_smoke.py`) covering a FastAPI health-check readiness path.
Backend unit and policy coverage exists for analysis, coach context, coach schema, intervals, models, playbooks, policy validator, profile, risk, strava auth, stream metrics, sync integration, units/cadence, webhooks, and workout matching.
Integration-tagged tests are excluded from the default regression run because they depend on local services or deeper cross-layer setup.
Frontend regression runs via `npm run test`, which invokes `next lint` then `next build`; there is no Jest or component test runner configured.
Frontend smoke runs via `npm run smoke` (`scripts/smoke.mjs`), which boots a mock API on `3001` and a Next dev server on `3100` and verifies core routes load.
There is no CI workflow file under `.github/workflows/`; `.github/` contains `copilot-instructions.md` and a `hooks/` directory only.
Major gap: no automated frontend unit or component tests beyond the build-time lint and smoke route checks.
Major gap: no end-to-end test that exercises a real Strava-to-coach-report flow.

## Maintenance Checklist

Update this file when a new top-level path is added under `backend/app/`, `frontend/app/`, `frontend/components/`, or `frontend/lib/`.
Update this file when a new SQLAlchemy model, alembic migration that adds a table, or new API router is introduced.
Update this file when a direct dependency is added or removed in `backend/pyproject.toml` or `frontend/package.json`.
Update this file when the deterministic policy validator's rule set materially changes or a new policy gate is introduced.
Update this file when the Strava ingestion, processing pipeline, or coach pipeline gains or loses a stage.
Update this file when the local runtime topology (ports, services, env vars) changes.
Update this file when planned-workout capture is implemented and `_extract_planned_workout` starts returning real data.
