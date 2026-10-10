@north-star.md
> Domain vocabulary lives in `CONTEXT.md`. This file describes the current implementation; the glossary describes the language.
> The North Star above is the standing test for every coach-LLM decision (loaded via this file's import); apply it alongside the workflow whenever a change touches what the coach receives or is told.

## Product Summary
Running Coach is a multi-user app that connects to Strava, ingests activities, computes training signals, and produces opinionated post-run coaching.
Each runner signs in with Clerk social login, where the verified email is the durable identity, and connects their own Strava account.
It runs locally with Postgres and Redis in docker compose and the app processes on the host, or deployed on Railway (backend, Postgres, Redis) and Vercel (frontend).
The core flow is: connect Strava, sync activities, deep-process a run, then read derived metrics and an LLM coach report, with a conversational coach and a training schedule alongside.

## Domain Concepts
A `User` owns a `UserProfile`, a `StravaAccount`, and a nullable `telegram_chat_id` for notification routing.
`UserProfile` nullable fields such as `weight_kg`, `height_cm` and `stated_pbs` mean NOT STATED rather than average, and are envelope-validated at the API.
An `Activity` is one Strava activity, with per-sample data in `ActivityStream` and one row of computed signals in `DerivedMetric`.
Classification is five orthogonal nullable axes (`effort`, `duration_class`, `structure`, `is_hilly`, `is_race`) rather than one label.
`effort_score` is cumulative training LOAD (Edwards-style zone-minutes), not intensity, so a long easy run scores high.
A `Block` groups temporally-contiguous activities into one training event by time gap, and an `Exchange` is its one two-stage coaching lifecycle row.
A `CoachingRelationship` is the one-row-per-user anchor holding the runner-declared Voice and Stance.
Voice and Stance are written only by `PUT /api/coach/voice` and `PUT /api/coach/stance`, and no background job infers or mutates either.
Voice flexes delivery only and Stance reweights emphasis only; neither touches the facts, the grounding data, or the safety floor.
A `CheckIn` captures subjective post-run input (RPE, pain, sleep quality, notes) against an `Activity`.
A `CoachReport` is the cached LLM analysis for an `Activity`, keyed `(activity_id, prompt_id, schema_version)` so a prompt change retains prior reports.
That key is a partial unique index scoped to `superseded_at IS NULL`, so every read path must filter `superseded_at IS NULL`.
Its `report` JSON is either the legacy structured `CoachReportContent` (schema 1.2) or the prose `CoachMessageReport` (schema 2.0).
One evolving `CoachReport` row holds both exchange stages: the opener writes `opener_message`, and the fuller turn fills `message` in place.
A `Thread` is a runner-initiated conversation, and its `CoachChatMessage` rows store `asked_from`, `tools_used`, and `skills_used` provenance.
A `CoachChatMessage` with role `event` records a confirmed proposed action, and `threads.CONVERSATIONAL_ROLES` keeps it out of everything that reads what was said.
A `RunnerBaseline` stores rolling per-user typicals plus trends bucketed by effort, terrain and temperature band.
A `RunnerMemory` is the durable memory profile of facts the runner stated, the citable `Stated memory` tier that yields to this run's `DerivedMetric` on conflict.
A `UserMaterial` is a runner-uploaded markdown file whose untrusted `raw_text` never enters a prompt or the API, only its strict-coerced `distilled` record.
A `GoalRace` is the runner's stated goal, whose `A`/`B`/`C` `priority` is the runner's ranking and never a claim about ability.
A `TrainingPlan` holds rules and week shapes, with at most one `active` plan per user enforced by the writer rather than a DB constraint.
`UserProfile.max_activities_per_day` is the runner's own daily limit, walks included, merged into a plan's rules wherever they are checked rather than stored on the plan, which a redraft rewrites.
`superseded_at` on a plan is written only by `activate_plan`, so a superseded plan stays restorable.
A `Season` is the coach's read of every goal, stale only when `goals_fingerprint` changes on a goal edit, addition or deletion.
A `PlannedSession` stores an inclusive `[window_start, window_end]`, and placement and the effective window are derived at read time, never stored.
A `PeriodReport` is a runner-requested review over a chosen period and discipline set.
A `RecoveryDay` is one runner-night from a device, where a null field means NOT MEASURED, and nothing in the coach pack reads it yet.

## Scope
The backend exposes JSON endpoints under `/api`, one router per resource in `backend/app/api/`.
Strava ingestion runs through manual sync (`POST /api/sync`, summaries only), incoming webhooks, a resumable history import, and a per-user self-heal check.
Strava webhook events are authenticated before any side effect by `_event_is_authentic`, binding the owner, the subscription id when set, and the activity's owner.
Telegram is the only notification channel, with RPE and pain taps writing the same `CheckIn` the in-app endpoint writes.
Coach threads stream turns over SSE from `POST /api/coach/threads/messages`, and a proposed action is applied only through `POST /api/coach/threads/actions/confirm`.
The schedule drafts a season and a plan through `generate_schedule_job`, and `amend_plan` rewrites only the sessions inside a named window.
The Garmin recovery sync runs for the deployment owner only, behind `GARMIN_SYNC_ENABLED` (default off).
`DELETE /api/account` removes the Clerk user first and touches nothing locally if that fails.
The history import takes summaries and deterministic analysis only: no streams, no coach report, and no notification.
Block split and merge corrections inherit the exchange sentinels, so no coaching message re-fires.
The frontend renders home, activity detail, profile, trends, `/load`, `/schedule`, and `/period-reports`.
The backend verifies a Clerk session JWT on each request, and `require_current_user` scopes every application router to the user.
`BasicAuthMiddleware` is the frontend-to-backend service secret, not the user gate, and exempts health, webhooks, and the Strava OAuth callback.
In production a missing Clerk config fails closed with 503, and the web process refuses to boot without `CLERK_JWKS_URL`, `BASIC_AUTH_USER`, and `BASIC_AUTH_PASSWORD`.
The frontend reaches the backend through `BACKEND_URL` server-side and the proxy `frontend/app/api/[...path]/route.ts` client-side, both injecting Basic credentials.

## Important Constraints
Settings come from `backend/.env` via `pydantic-settings`, and the app will not boot without `DATABASE_URL`.
PRODUCTION DOES NOT RUN THE CODE DEFAULTS, so any reasoning about what the coach receives starts from `backend/.env.example`'s prod-parity block.
Eleven coach inputs are OFF in the deployed environment: `COACH_ADHERENCE_ENABLED`, `COACH_CONTINUITY_ENABLED`, `COACH_HOUSE_SCHOOLS_ENABLED`, `COACH_LONGITUDINAL_ENABLED`, `COACH_PLAYBOOK_ENABLED`, `COACH_PREVIOUS_30D_ENABLED`, `COACH_PRIOR_REPORTS_ENABLED`, `COACH_SALIENCE_ENABLED`, `COACH_SLEEP_QUALITY_ENABLED`, `COACH_STOPS_ANALYSIS_ENABLED`, `COACH_USER_MATERIALS_ENABLED`.
That block is the source of truth for the deployed prompt id and coach switches, and `make diagram-check` pins both diagrams to it.
The code default prompt is `coach_message_v8`, while the prod-parity block declares `coach_message_lean_grouped_v12`.
Selecting a prompt is a pure `COACH_PROMPT_ID` flip, and every earlier prompt id stays registered so a rollback is config only.
Most `COACH_*_ENABLED` switches remove one named item from what the coach receives, while `COACH_THREADS_ENABLED` and `COACH_PERIOD_REPORT_ENABLED` gate a surface instead.
A switch the prod-parity block does not declare runs at its code default, which is False for `COACH_ADHERENCE_ENABLED` and `COACH_PRIOR_REPORTS_ENABLED`, so absent never means on.
`COACH_RECEIPT_CADENCE` is on in production, replacing the LLM opener with an instant deterministic receipt plus one full report about `BLOCK_GAP_SECONDS` after the session.
`SCHEDULE_ENABLED` gates the schedule screen, while `COACH_SCHEDULE_ENABLED` only drops the schedule from what the coach receives.
Model lanes (`COACH_CHAT_MODEL_ID`, `COACH_VOICE_MODEL_ID`, `COACH_PERIOD_MODEL_ID`, `COACH_SCHEDULE_MODEL_ID`) fall back to `COACH_MODEL_ID` when unset.
LLM and Strava spend budgets degrade the app silently at their limits and are armed in production by default; `docs/deployment/topology.md` lists every such limit.
`RQ_JOB_TIMEOUT_SECONDS` is the queue default and is passed explicitly on `queue.enqueue_in`, because a two-stage generation outlives RQ's 180s default.
An unbound user's notifications fall back to the global `TELEGRAM_CHAT_ID` only for the identified deployment owner, and otherwise fail closed.
Env vars are per Railway service, so a var set on `web` is not set on `worker`.
Platform training-load numbers (Strava Fitness, Garmin Training Load) are validation-only and never seed our own readiness model, because they are a different unit.
Every stored `context_pack` is re-parsed through `load_stored_pack`, and no stored pack is ever migrated or deleted.
Postgres is on host port `5433`, Redis `6379`, backend `8000`, and frontend `3000`.

## Architecture Summary
The backend is a FastAPI app (`app/main.py`) using SQLAlchemy 2.x on Postgres, with Alembic migrations in `backend/alembic/versions/`.
Background work runs on Redis-backed RQ through `python -m app.worker`, whose embedded scheduler drains every retry and deferred `queue.enqueue_in` job.
The Strava integration is a port (`StravaPort`) with an HTTP adapter and an in-memory adapter for tests.
Analysis is a pipeline in `app/services/analysis/`, where `stages.py` declares what each stage reads and writes and a contract break fails at import.
The `DerivedMetric` upsert writes every column unconditionally, so a stage that abstains overwrites the prior value.
Webhook and self-heal converge on `app/jobs/process_new_activity.py`: ingest, analyze, assign block, then dispatch to the active cadence in `app/jobs/cadence/`.
The coach report chains `context.py`, `llm.py`, `validator.py`, `service.py`, and finally `voice_rewrite.py`.
`service.py` caches the result in `CoachReport` and sets `is_fallback=True` on LLM failure, parse failure, or a medical overreach that survives the policy retry.
The report is generated VOICELESS, and `voice_rewrite.py` re-says it in the runner's voice without adding a fact, dropping a safety item, or changing a verdict.
Every rewrite failure serves the voiceless baseline, which stays in `message` for the eval harness and learning loop.
The deterministic policy validator must not be bypassed; its rules are shared `check_*` functions assembled for the structured report, the prose report, and streamed chat replies.
Rule 5 is the medical-scope floor: no dose advice, no diagnosis, no directive medication advice, and no asserted clinical condition.
Rules 7 and 8 reject citing a `corpus.*` field path as report evidence, while the runner memory profile is deliberately citable.
A medical overreach in chat withholds the reply and serves `MEDICAL_REDIRECT_MESSAGE`.
`services/coach/signal_registry.py` is the one declaration of every pack section, its group, prompt feature gate, and kill switches, from which the pack and its views derive.
Under a grouped prompt the pack is re-nested into `this_run`, `right_now`, `the_runner`, `our_thread`, and `how_to_coach`, plus top-level `safety_rules`.
`coach_framing.coach_llm_view` builds the outgoing LLM message for both the report and chat, so both models read an identical pack.
`prompt_features.PROMPT_FEATURES` is the capability manifest per prompt id, and a section is emitted only under a prompt carrying its feature.
`prompt_clauses.py` owns the live prompt text as named clauses, `prompt_archive.py` holds every retired prompt verbatim, and `compose` refuses a clause set without the safety floor.
Adding a prompt version is two declaration rows (`PROMPT_FEATURES` and `PROSE_VARIANTS`) plus its own test, editing no prior version's test.
The prose report's one output tool is `output_contract.RECORD_COACH_TAIL_TOOL`, a hand-frozen schema shared by every prompt id.
The chat turn is screen-aware through a server-resolved `ScreenPointer`, typed so no fact can travel from the client.
Coaching skills are code-resident procedures the model loads with the `load_coaching_skill` tool, and `query_tools.py` holds the chat's data tools.
`training_metrics.py` declares every measure `get_training_metric` serves, and its test fails when a stored per-activity field is neither read by a measure nor listed in `STORED_FIELDS_EXCLUDED`.
The `add_goal` proposed action is the only way a found event becomes a `GoalRace`, written on confirm with no `booked` bit and no target time from the model.
A report's schedule offer is stored without a token, and `report_offer.mint_report_offer` mints the single-use token only when the owner reads the report.
A runner's untrusted text is made safe by structured output, strict coercion, and raw text never entering a prompt, which the schedule draft and period report reuse.
Durable memory is rebuilt from source each time without reading its stored value, so the coach cannot echo its own inferences into it.
`coach/exchange_lifecycle.py` is the single owner of every `Exchange` transition and the at-most-once notification invariant.
`services/activity_facts.py` is the single home of the fact stream, so the coach pack and the Trends page cannot disagree about a number.
`services/weeks.py` is the single week-boundary definition, parameterized by the runner's `week_starts_on`.
The schedule package computes no training total of its own: actuals come from `activity_facts`, weeks from `weeks.py`, and typical volume from `coach/volume.py` and `norms.py`.
Weeks past the ones the model writes as sessions are shapes written by code from the season's phases, never figures a model typed.
`plan_validator` and `week_check` hold each written week to its limits and to its frame.

## Key Dependencies
`fastapi`, `uvicorn`: HTTP server and ASGI runtime for the backend.
`sqlalchemy`, `psycopg`, `alembic`: ORM, Postgres driver, and schema migrations.
`pydantic`, `pydantic-settings`: schemas and environment configuration.
`redis`, `rq`: the background job queue.
`anthropic`: Claude API client, pinned below its next major so a new major is adopted deliberately.
`httpx2`: the `anthropic` SDK's HTTP layer, declared because `RetryLadder` matches its `RemoteProtocolError`.
`httpx`: outbound HTTP for Strava, Telegram, and the Clerk JWKS fetch; `pyjwt` verifies the Clerk session JWT.
`garminconnect`: unofficial Garmin client for the owner-only recovery sync, imported lazily.
`next`, `react`, `@clerk/nextjs`, `react-markdown`: the frontend, its session gate, and coach report rendering.

## Project Structure
`backend/app/api/` holds one router per resource, and `deps.py` resolves every owned resource (`OwnedActivity`, `OwnedThread`, and siblings) before the handler body runs.
`backend/app/core/` holds `config.py` (the typed settings), `queue.py`, Clerk auth, OAuth state signing, and observability.
`backend/app/models/` holds one ORM model per file, and `backend/app/schemas/` one Pydantic schema file per domain.
`backend/app/services/strava_ingestion/` holds the Strava port, adapters, persistence, token refresh, and HR-zone sync.
`backend/app/services/analysis/` is the metrics pipeline.
`services/intents.py` is the single home of the stated-intent vocabulary, which the frontend renders from `intent_options` rather than a copy of its own.
`upsert_activity` preserves stored laps and `best_efforts` across a summary-only re-sync.
`backend/app/services/coach/` owns the LLM coach, and its `__init__.py` is the module map.
`turn.py` is the envelope every coach generation shares: model lane, metered client, and the `over_budget` gate.
`backend/app/services/schedule/` is the schedule package: season, plan drafting, amendment, validation, completion, and the coach's view of it.
`backend/app/services/notifications/` holds the notifier port, the Telegram adapter, and the tap-token codec.
`backend/app/jobs/` holds the RQ jobs, whose entrypoint module paths must not move because RQ serializes a deferred job as its `module.function` string.
`backend/scripts/` holds `pre_deploy.py` (env preflight, then migrations when `RUN_MIGRATIONS=true`, set on `web` only), `seed_from_prod.py`, and the eval and verification scripts.
`frontend/app/` holds the routes and the API proxy, and `frontend/components/` the panels and the `coach/`, `trends/`, `load/`, and `schedule/` subfolders.
`frontend/middleware.ts` excludes `apple-icon` by name, because that PWA path carries no dot and would otherwise sit inside the Clerk gate.
`docs/adr/` holds the architecture decision records, `docs/deployment/` the deployment topology and runbooks, and `docs/diagrams/` the generated coach data-flow diagrams with their drift guard.

## Testing Overview
`make backend-test` runs pytest excluding tests marked `integration`, on SQLite via `tests/conftest.py`, which ignores `backend/.env` so local runs resolve the code defaults CI resolves.
Frontend regression is `npm run test` (`next lint` then `next build`), and `npm run smoke` boots a mock API and verifies core routes load; there is no component test runner.
`make alembic-check` catches model/migration drift against a real Postgres, which the suite cannot see because it builds its schema with `create_all`.
`make diagram-check` fails when what the coach receives changes without the diagrams being regenerated, and `backend/tests/test_diagram_drift.py` proves each of its checks can fail.
`tests/test_route_ownership_802.py` fails when a route taking an owned-resource path parameter does not resolve it through `deps.py`.
Route sweeps enumerate routes through `backend/tests/_route_table.py`, which proves its enumeration is not empty, because a sweep over nothing passes silently.
The eval harness scores stored reports against rubric assertions scoped to the current `(prompt_id, schema_version)`.
`backend/tests/test_context_budget_907.py` enforces this file's size budget.
CI is `.github/workflows/deploy.yml`, running `backend-test`, `frontend-test`, `alembic-check`, and a push-only `post-deploy-verify`.
There is no end-to-end test of a real Strava-to-coach-report flow.

## Real-Data Verification
No Strava OAuth app exists for local, so local data comes only from a production snapshot; both paths are in `docs/testing/local-seed.md`.
Per-branch preview deployments point at the single production backend and database, so any write on a preview mutates production data.
`make seed-local` copies a production snapshot into the local database, reading from the source only and refusing any non-local target.
The seed redacts Strava tokens by default, so "Sync Now" fails on a seeded local database.
`make verify-local` runs the app with local no-auth flags for browser checks, which fail closed in production, and does not start the worker.
The deployed-only Strava OAuth and Telegram handshakes have a manual runbook at `docs/testing/deployed-handshake-verification.md`.

## Maintenance Checklist
Invoke the `aiw-project-context-management` skill before editing this file.
Write exactly one sentence per line, and never grow an existing line to carry a new fact.
The file is full at its budget, so adding a line means choosing which line leaves.
State what exists, where it lives, and what it is for; the code states how it works.
Update this file when a model, router, top-level path, direct dependency, pipeline stage, coach switch, or production switch state is added, removed, or changed.
