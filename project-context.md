> Domain vocabulary lives in `CONTEXT.md`. This file describes the current implementation; the glossary describes the language.

## Product Summary

Running Coach is a single-user MVP that connects to Strava, ingests running activities, computes training signals, and produces opinionated post-run analysis.
The intended user is an individual runner connected to their own Strava account; the app runs either locally via docker compose or as a deployed single-user instance on Railway (backend, Postgres, Redis) and Vercel (frontend). Phase 2 introduces multi-user signup per ADR 0005.
The core flow is: connect Strava → sync activities → deep-process a run → view derived metrics and an LLM-generated coach report on the activity page.

## Domain Concepts

A `User` owns a `UserProfile` (goal, experience, weekly volume, max HR, races, injuries) and a linked `StravaAccount` (OAuth tokens, athlete id).
An `Activity` is a single Strava activity record owned by a `User`, identified by `strava_activity_id`.
An `ActivityStream` holds per-sample time-series data (HR, pace, cadence, power) attached to an `Activity`.
A `DerivedMetric` row attaches one set of computed signals to one `Activity` (activity class, effort score, pace variability, HR drift, time-in-zones, stops, efficiency, intervals, flags, confidence, risk, discount signals).
A `CheckIn` captures subjective post-run input from the user against an `Activity`.
A `CoachReport` is the cached structured LLM analysis for an `Activity`, keyed by `(activity_id, prompt_id, schema_version)` so a prompt/schema change retains prior-version reports instead of overwriting them; `CoachChatMessage` rows form a follow-up conversation against that activity.
A `RunnerBaseline` (one row per user) stores rolling baselines used for comparison and drift detection: per-user scalar typicals plus a `bucketed_trends` JSON map of EF and HR-drift trends bucketed by `effort|terrain|temperature-band`, computed by `services/analysis/baseline.py` and recomputed at the end of `analyze`; a bucket abstains until it has `MIN_SAMPLES_FOR_TREND` (4) like-for-like samples.

## Scope

The backend exposes JSON endpoints under `/api` for health, Strava OAuth, profile CRUD, activity listing/detail, sync, deep processing, historical stream backfill, intent labelling, check-ins, trends, coach report, and coach chat.
Strava ingestion supports both manual sync (`POST /api/sync`) and incoming webhooks (`/api/webhooks/strava`); webhook `aspect_type=create` events enqueue `process_new_activity_job` (ingest → analyze → coach → notify), `update` events enqueue `sync_activity_job` (re-ingest only), and `delete` events soft-delete the activity row.
`HTTPStravaAdapter.list_recent_activities` paginates `/athlete/activities` until a short page, capped at `_MAX_PAGES` (40), so a window wider than one page no longer silently truncates to the first 50 activities.
`POST /api/sync` takes `since_days` (default 30); windows up to 30 days fetch streams (full deep processing), while larger windows import activity summaries only (`ingest_recent_activities(fetch_streams=False)`) as a rate-limit-safe historical backfill since each activity's streams cost a separate Strava call.
`POST /api/activities/backfill-streams` enqueues `backfill_streams_job`, a manually-triggered self-pacing job that fills the stream-derived-analysis gap left by summary-only imports: each batch fetches streams and re-runs analysis for up to `BACKFILL_BATCH_SIZE` activities that still lack them, marks each via `Activity.streams_backfilled_at` (so it is resumable and attempted exactly once), schedules its successor via `rq-scheduler` `enqueue_in` while work remains, and never notifies.
Incoming webhook events are authenticated before any side effect by `_event_is_authentic` in `app/api/webhooks.py`: `owner_id` must match a connected `StravaAccount`, and `subscription_id` must match `STRAVA_WEBHOOK_SUBSCRIPTION_ID` when that setting is non-zero, otherwise the request is rejected with 403.
A polling fallback (`poll_for_new_activities_job`) discovers activities Strava webhook missed and converges on the same pipeline; it runs every `POLLING_INTERVAL_SECONDS` via an `rq-scheduler` process.
Deep processing classifies the activity, computes metrics from streams (smoothing, splits, intervals, stops, efficiency, risk), and runs workout matching against any planned workout.
The coach pipeline builds a context pack, calls Anthropic, validates the response against a Pydantic schema, then runs a deterministic policy validator before storing the report; LLM/parse failures persist a fallback `CoachReport` with `is_fallback=True`.
Coach-report email delivery is gated by `SMTP_HOST` and `NOTIFY_TO`; with both unset the notifier is a no-op. Successful delivery sets `Activity.coach_notification_sent_at`, which is the dedup sentinel for at-most-once-per-activity email semantics.
The frontend renders an activity list on the home page, a per-activity detail page with charts and panels, a profile page, and a trends page with filters and chart views.
Planned-workout capture is not yet implemented; `_extract_planned_workout` in `services/analysis/_orchestrator.py` returns `None` as a placeholder.
There is no multi-user auth layer; the backend assumes a single local user and auto-creates one on first profile read.
Railway runs the backend as three services off one image (`web` FastAPI, `worker` RQ jobs, `scheduler` rqscheduler) plus managed Postgres and Redis; Vercel hosts the Next.js frontend.
The Vercel frontend reaches the backend over HTTP: server components call `BACKEND_URL` directly and the catch-all route handler `frontend/app/api/[...path]/route.ts` proxies client-side `/api/*` calls, both injecting HTTP Basic credentials server-side so the browser never sees them.
The backend gates all routes with `BasicAuthMiddleware`, exempting `/api/health`, `/api/webhooks`, and `/api/auth/strava/callback`; this Phase 1 layer is replaced by ADR 0005 magic-link sessions in Phase 2.
Build and deploy config lives on the platforms rather than in an in-repo deploy manifest; the repo's only committed automation is the CI workflow under `.github/workflows/`.
Locally `docker compose` provides only Postgres and Redis, while the host runs `uvicorn`, `rq worker`, an optional `rqscheduler`, and `next dev`.
The full production and local-dev topology, the connection seam, and per-service env var ownership are documented in `docs/deployment/topology.md`.

## Important Constraints

Settings come from `backend/.env` via `pydantic-settings`; `DATABASE_URL` is required and the app will not boot without it.
Anthropic access requires `ANTHROPIC_API_KEY`; the coach model id and prompt id are configured via `COACH_MODEL_ID` and `COACH_PROMPT_ID`.
Coach-report notifications pick a channel by config (`_active_channel` in `app/services/notifications/__init__.py`): Telegram when `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are both set, else email when `SMTP_HOST` + `NOTIFY_TO` are both set, else a no-op. Telegram is the deployed channel because Railway blocks outbound SMTP from the worker (#127); the Bot API runs over HTTPS. Email transport is configured via `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_STARTTLS`.
The polling fallback interval is `POLLING_INTERVAL_SECONDS` (default 120s, ~720 Strava calls/day per account, well under Strava's 1000/day limit).
The polling lookback window is `POLLING_LOOKBACK_SECONDS` (default 604800s / 7 days); each poll asks Strava for activities within this window, so an ingestion outage shorter than the window self-heals while a longer gap needs a manual `POST /api/sync` backfill. For a single recreational runner the window stays within one page, so it does not raise the steady-state call budget.
The historical stream backfill is paced by `BACKFILL_BATCH_SIZE` (default 20, activities per batch) and `BACKFILL_BATCH_PAUSE_SECONDS` (default 300, delay before the next batch); the default 20 stream calls per 5 minutes plus polling stays under Strava's 100-requests/15-min ceiling, and because the backfill is one-time its daily cost is just the backlog size and converges to zero.
CORS origins are configured via `CORS_ALLOWED_ORIGINS` (comma-separated, default `http://localhost:3000,http://localhost:8000`), parsed by `Settings.cors_allowed_origins_list` and applied in `app/main.py`.
Error tracking is logs-only by default; Sentry capture is opt-in, requiring the `observability` extra (`sentry-sdk[fastapi]`) installed and `SENTRY_DSN` set, otherwise `init_sentry` in `app/core/observability.py` is a no-op.
The deterministic policy validator in `services/coach/validator.py` has five rules: it rejects LLM output that misses questions for a null check-in, references uncalibrated HR zones, cites a risk flag not in the flags array, claims specific interval execution counts (e.g. "8x400m", "executed 8") under low detection confidence, and (the medical-scope rule) gives dose advice, uses diagnosis verbs, issues directive medication advice, or asserts a clinical condition about the runner, while still permitting interpretive metric correction and a non-diagnostic referral nudge; per `ai-workflow.md` this gate must not be bypassed.
When data confidence is low, downstream analysis is expected to default to conservative output (documented intent in `README.md`).
Postgres is exposed on host port `5433` (mapped from container `5432`); Redis on `6379`; backend on `8000`; frontend on `3000`.
Backend test baseline excludes tests marked `integration` (pytest marker registered in `pyproject.toml`).

## Architecture Summary

The backend is a FastAPI app (`app/main.py`) wiring routers from `app/api/*` under the `/api` prefix.
Persistence uses SQLAlchemy 2.x ORM with a Postgres database; schema migrations live under `backend/alembic/versions/`.
Background work runs via Redis-backed RQ; jobs are defined in `app/jobs/` and a worker is started with `rq worker`. A separate `rqscheduler` process registers and fires the recurring polling job; bootstrap it once via `python -m app.jobs.scheduler`.
The Strava integration is a port (`StravaPort` in `app/services/strava_ingestion/port.py`) with `HTTPStravaAdapter` and `InMemoryStravaAdapter`; the ingestion module (`app/services/strava_ingestion/ingestion.py`) owns persistence and orchestration on top of that port (ADR 0002, ADR 0003).
Analysis is a pipeline of pure-ish functions in `app/services/analysis/` (smoothing, metrics, classifier, splits, intervals, stops, flags, risk, discount signals, workout matching) composed by `_orchestrator.py`; the public surface is `analyze` and `analyze_with_streams`. The discount-signals stage (`discount_signals.py`) is a deterministic, pipeline-owned annotation marking when HR drift is likely inflated by heat (`average_temp` from `raw_summary`), terrain (`is_hilly`), or the opt-in `UserProfile.stimulant_use` flag, so the coach discounts it rather than reading it as fatigue; it degrades gracefully and never fabricates a confound.
The coach layer is `context.py` (builds the pack) → `llm.py` (Anthropic client) → Pydantic schemas in `app/schemas/coach.py` → `validator.py` (policy gate) → `service.py` (caches result in `CoachReport`, with `is_fallback=True` on LLM/parse failure).
The notifications layer is a port (`NotifierPort` in `app/services/notifications/port.py`) with `TelegramNotifier` (HTTPS Bot API via `httpx`), `SMTPNotifier` (stdlib `smtplib`), `InMemoryNotifier`, and `NoOpNotifier`. `build_coach_notification` in the package `__init__.py` renders a `Notification` for the active channel, keeping the pipeline channel-agnostic; `email_template.py` renders the email (subject + HTML + plaintext) and `telegram_template.py` renders the Telegram message (title + plaintext body + activity URL).
The webhook/polling convergence is `app/jobs/process_new_activity.py`: ingest → analyze → coach generate → notify, gated by `Activity.coach_notification_sent_at` (ADR 0004).
The frontend is a Next.js 14 App Router project; pages live under `frontend/app/`, reusable UI under `frontend/components/`, and the typed API client and shared types under `frontend/lib/`.
Data flow: Strava API → strava_ingestion → Activity/ActivityStream rows → analysis pipeline → DerivedMetric → coach service → CoachReport → either (a) frontend via `/api/activities/{id}/coach-report`, or (b) notifications via the pipeline job's email send.

## Key Dependencies

`fastapi`, `uvicorn`: HTTP server and ASGI runtime for the backend.
`sqlalchemy`, `psycopg`, `alembic`: ORM, Postgres driver, and schema migrations.
`pydantic`, `pydantic-settings`: request/response schemas and environment configuration.
`httpx`: outbound HTTP client used for Strava and Anthropic calls.
`redis`, `rq`: job queue for sync and processing background work.
`rq-scheduler`: schedules the recurring polling job that catches activities Strava webhook missed.
`numpy`: numerical computation in the processing pipeline (smoothing, metrics, intervals).
`anthropic`: Claude API client used by the coach service.
`python-multipart`: form parsing required by FastAPI for non-JSON request bodies.
`sentry-sdk[fastapi]` (optional `observability` extra): error tracking, installed only when Sentry capture is enabled.
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
`backend/app/api/` contains one router per resource: `health.py`, `auth.py`, `profile.py`, `activities.py`, `webhooks.py`, `trends.py`, `coach.py`, `debug.py`.
`backend/app/core/config.py` defines the typed settings object; `backend/app/core/queue.py` holds RQ queue setup; `backend/app/core/observability.py` provides `init_logging` and `init_sentry` (import-guarded, no-op without the optional SDK).
`backend/app/db/base.py` defines the SQLAlchemy `Base`; `backend/app/db/session.py` provides `SessionLocal` and the `get_db` dependency.
`backend/app/models/` holds one ORM model per file (`activity`, `activity_stream`, `checkin`, `coach_chat_message`, `coach_report`, `derived_metric`, `runner_baseline`, `strava_account`, `user`, `user_profile`) with a barrel `__init__.py`.
`backend/app/schemas/` holds Pydantic request/response schemas, one file per domain (`activity`, `chat`, `checkin`, `coach`, `detail`, `profile`, `sync`, `trends`, `user`).
`backend/app/services/strava_ingestion/` holds the Strava port + adapters (`port.py`, `http_adapter.py`, `in_memory_adapter.py`) and the ingestion module (`ingestion.py`) that persists activities and streams.
`backend/app/services/analysis/` is the metrics pipeline (`_orchestrator.py` composes `smoothing.py`, `metrics.py`, `classifier.py`, `splits.py`, `intervals.py`, `stops.py`, `flags.py`, `risk.py`, `discount_signals.py`, `workout_matching.py`, `_training_context.py`); the public surface is `analyze` and `analyze_with_streams`. `baseline.py` is the M2 RunnerBaseline trend substrate (pure bucketing/trend helpers plus a `recompute_runner_baseline` persistence service that `analyze` calls at the end, guarded so a baseline failure never breaks analysis).
`backend/app/services/coach/` owns the LLM coach (`context.py`, `prompts.py`, `llm.py`, `validator.py`, `service.py`, `chat.py`).
`backend/app/services/notifications/` owns the notifier port + adapters (`port.py`, `telegram_adapter.py`, `smtp_adapter.py`, `in_memory_adapter.py`, `noop_adapter.py`), the channel selection + composer (`__init__.py`), and the per-channel templates (`email_template.py`, `telegram_template.py`).
`backend/app/services/trends.py` produces aggregated trend data; `backend/app/services/units/cadence.py` normalises cadence units; `backend/app/services/activity_queries.py` holds shared activity-query helpers.
`backend/app/jobs/strava_sync.py` defines the legacy sync RQ job entries; `backend/app/jobs/process_new_activity.py` is the convergence pipeline job; `backend/app/jobs/polling.py` is the Strava polling fallback; `backend/app/jobs/backfill_streams.py` is the manually-triggered self-pacing historical stream-analysis backfill; `backend/app/jobs/scheduler.py` bootstraps `rq-scheduler`. `backend/app/worker.py` is the worker bootstrap.
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
`Makefile` exposes `smoke`, `backend-smoke`, `frontend-smoke`, `test`, `backend-test`, `frontend-test`, `seed-local`.

## Testing Overview

Backend tests run via `python -m pytest`; the global baseline command is `make backend-test`, which excludes tests marked `integration`.
Backend smoke is a single file (`backend/tests/test_smoke.py`) covering a FastAPI health-check readiness path.
Backend unit and policy coverage exists for analysis, coach context, coach schema, coach fallback flag, email template, notifier port, SMTP notifier, pipeline job (`process_new_activity`), polling job, webhook dispatch, end-to-end pipeline, intervals, models, playbooks, policy validator, profile, risk, strava auth, stream metrics, sync integration, units/cadence, webhooks, and workout matching.
Integration-tagged tests are excluded from the default regression run because they depend on local services or deeper cross-layer setup.
Frontend regression runs via `npm run test`, which invokes `next lint` then `next build`; there is no Jest or component test runner configured.
Frontend smoke runs via `npm run smoke` (`scripts/smoke.mjs`), which boots a mock API on `3001` and a Next dev server on `3100` and verifies core routes load.
CI runs via `.github/workflows/deploy.yml` (workflow name `ci`) on push and pull requests to `main`, with a `backend-test` job (`make backend-test` on Python 3.12) and a `frontend-test` job (`npm run test` on Node 20); `.github/` also contains `copilot-instructions.md` and a `hooks/` directory.
Major gap: no automated frontend unit or component tests beyond the build-time lint and smoke route checks.
Major gap: no end-to-end test that exercises a real Strava-to-coach-report flow.

## Real-Data Verification

There is no Strava OAuth application for dev or local, so the connect-then-sync path cannot bootstrap data outside production; real-data verification therefore uses one of the two paths below, both documented in `docs/testing/local-seed.md`.
Path 1 (deployed): the production Vercel app (`https://ai-running-coach-eta.vercel.app/`) and the per-branch preview deployments are publicly reachable and render real synced data because the frontend injects the backend's HTTP Basic credentials server-side, so browser automation against them verifies any UI or read-path change with no local setup.
Preview deployments are not isolated environments: they point at the same single production backend and Postgres/Redis as production, so any write triggered on a preview (sync, destructive migration, delete) mutates production data.
Path 2 (local): `make seed-local` runs `backend/scripts/seed_from_prod.py`, which resets the local schema to the branch's migration head and copies a production snapshot (users, profile, Strava account, activities, streams, derived metrics, coach reports, chat, check-ins) so the analyze -> coach -> frontend chain runs offline against real data; `SEED_ARGS="--activities N"` bounds the snapshot.
The seed redacts Strava tokens by default so local cannot call Strava and cannot rotate the production refresh token, which means the home page "Sync Now" action fails on a seeded local DB; `SEED_ARGS="--with-live-tokens"` is the opt-in for exercising a real sync and shares the production token.
`seed_from_prod.py` reads the source URL from `SEED_SOURCE_URL` (the `make` target injects Railway's `DATABASE_PUBLIC_URL` via the project token at `~/.railway_token`), only ever reads from the source, and refuses any target URL that is not `localhost`/`127.0.0.1`.
Local verification runs the backend via `uvicorn app.main:app --port 8000` and the frontend via `npm run dev` (port 3000) against the docker-compose Postgres/Redis; `BasicAuthMiddleware` is a no-op locally when `BASIC_AUTH_USER`/`BASIC_AUTH_PASSWORD` are unset, so local API calls need no credentials.

## Maintenance Checklist

Update this file when a new top-level path is added under `backend/app/`, `frontend/app/`, `frontend/components/`, or `frontend/lib/`.
Update this file when a new SQLAlchemy model, alembic migration that adds a table, or new API router is introduced.
Update this file when a direct dependency is added or removed in `backend/pyproject.toml` or `frontend/package.json`.
Update this file when the deterministic policy validator's rule set materially changes or a new policy gate is introduced.
Update this file when the Strava ingestion, processing pipeline, or coach pipeline gains or loses a stage.
Update this file when the local runtime topology (ports, services, env vars) changes.
Update the Real-Data Verification section when the seeding script, its safety defaults, or the deployed/preview environment topology changes, or when a dev/local Strava OAuth path is added.
Update this file when planned-workout capture is implemented and `_extract_planned_workout` starts returning real data.
