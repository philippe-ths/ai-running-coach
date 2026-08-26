@north-star.md
> Domain vocabulary lives in `CONTEXT.md`. This file describes the current implementation; the glossary describes the language.
> The North Star above is the standing test for every coach-LLM decision (loaded via this file's import); apply it alongside the workflow whenever a change touches what the coach receives or is told.

## Product Summary
Running Coach is a multi-user app that connects to Strava, ingests running activities, computes training signals, and produces opinionated post-run analysis.
Each runner signs in with social login (Clerk, ADR 0022; the verified email is the durable identity) and connects their own Strava account.
It runs locally with Postgres and Redis in docker compose and the app processes on the host, or deployed on Railway (backend, Postgres, Redis) and Vercel (frontend).
The core flow is: connect Strava, sync activities, deep-process a run, then read derived metrics and an LLM coach report on the activity page.

## Domain Concepts
A `User` owns a `UserProfile`, a linked `StravaAccount` (OAuth tokens, athlete id), and a nullable `telegram_chat_id` for per-user notification routing (ADR 0023).
`UserProfile` holds goal, experience, weekly volume, max HR, races, injuries, the Strava-sourced HR-zone lower bounds `hr_zones`/`hr_zones_source`, the runner's `week_starts_on` (Monday 0 or Sunday 6, null resolving to Monday), and the stated build `weight_kg`/`height_cm`.
`weight_kg`/`height_cm` are nullable floats meaning NOT STATED rather than average, envelope-validated at the API so a unit slip never reaches the coach as a fact.
`UserProfile.upcoming_races` is an untyped JSON blob no backend code reads; it is retained only because the frontend profile form still round-trips it.
An `Activity` is one Strava activity owned by a `User`, identified by `strava_activity_id`.
An `ActivityStream` holds per-sample time-series data (HR, pace, cadence, power) for an `Activity`.
A `DerivedMetric` row attaches one set of computed signals to one `Activity`: the five orthogonal classification axes, effort score, pace variability, HR drift, time-in-zones, stops, efficiency, intervals, flags, confidence, risk, discount signals, and a consolidated stream view.
Classification is five orthogonal axes rather than one label (ADR 0007): `effort`, `duration_class`, `structure`, `is_hilly`, and `is_race`, each nullable because a non-run populates only the axes that apply.
`DerivedMetric.stream_view` is a bounded (<=60 point) aligned HR/pace/grade/cadence downsample, a deferred-load JSON column pulled on demand and re-derived every analysis.
`effort_score` is the per-activity training-load primitive: Edwards-style zone-minutes (`Σ minutes-in-zone × zone-number`), estimated per data tier so it is one scale comparable and summable across activities with and without HR.
A `Block` (ADR 0011) groups a user's temporally-contiguous activities into one training event by time-gap clustering under `BLOCK_GAP_SECONDS`.
A `Block` carries `start_date`/`end_date`, a `primary_activity_id` (the run, else the longest member, frozen once a report exists), and a `user_corrected` audit bit; `activities.block_id` is the membership FK.
An `Exchange` is the two-stage lifecycle row for one `Block` (`block_id` UNIQUE), created with the block.
`Exchange` sentinels are `opened_at`, `opener_sent_at`, `fuller_sent_at` (set means CLOSED), `done_at` (the runner's explicit done tap), and per-activity `Activity.receipt_sent_at`.
A `CoachingRelationship` is the one-row-per-user durable anchor, auto-created on first profile read.
It carries the runner-declared Voice: `voice_preset`, the five 1-5 dials `voice_warmth`/`voice_humor`/`voice_force`/`voice_energy`/`voice_length`, and `voice_freetext`, all nullable and resolving to a balanced 3/3/3/3/3 default.
It also carries the runner-declared Stance: `stance_school` (a `corpus.SCHOOLS` key) and the two 1-5 dials `stance_data_sentiment` and `stance_process_outcome`, nullable and resolving to aerobic-base plus balanced 3/3.
Voice and Stance are runner-sovereign: their only writers are `PUT /api/coach/voice` and `PUT /api/coach/stance`, and no background job infers or mutates either.
Voice flexes delivery only and Stance reweights emphasis only; neither touches the facts, the grounding data, or the safety floor.
It also carries the pre-generated voiced receipt templates `receipt_templates`, `receipt_templates_voice_key`, and `receipt_templates_generated_at`.
A `CheckIn` captures subjective post-run input (RPE, pain, sleep quality, notes) against an `Activity`.
A `CoachReport` is the cached LLM analysis for an `Activity`, keyed `(activity_id, prompt_id, schema_version)` so a prompt or schema change retains prior-version reports.
That key is a PARTIAL unique index scoped to `superseded_at IS NULL`: a force regeneration supersedes the prior row and inserts a new current one, so every read path must filter `superseded_at IS NULL`.
Its `report` JSON holds one of two shapes (ADR 0009): the legacy structured `CoachReportContent` (schema 1.2) or the prose `CoachMessageReport` (schema 2.0).
`CoachMessageReport` carries a human prose `message`, a thin structured tail, an `opener_message`, a `schedule_fuller_turn` bit, and the voiced renderings `voiced_message`/`voiced_opener_message`.
One evolving `CoachReport` row holds both exchange stages: the opener writes `opener_message`, and the fuller turn fills `message` in place.
A `Thread` (ADR 0027) is the runner-initiated, relationship-scoped conversation unit: `user_id`, an optional `activity_id` anchor, a nullable runner-visible `title`, and `last_message_at`.
`CoachChatMessage` rows are the turns in a `Thread`, each storing `asked_from`, `tools_used`, and `skills_used` provenance.
A third `role` value, `event`, is the app's own record of a proposed action the runner confirmed, written only after the change was made.
`threads.CONVERSATIONAL_ROLES` filters `event` rows out of everything that reads what was SAID, and they reach the coach only through the system prompt's bounded `{confirmed_block}` ledger.
A `RunnerBaseline` (one row per user) stores rolling per-user scalar typicals plus a `bucketed_trends` JSON map of EF and HR-drift trends bucketed by `effort|terrain|temperature-band`.
A `RunnerMemory` (one row per user, ADR 0025) is the durable runner memory profile: five capped plain-language sections (`who_you_are`, `limits_and_constraints`, `goals_and_plans`, `what_works_for_you`, `lately`) plus `model_id`/`source_report_count`/`grounded_through` provenance.
It holds only facts the runner stated plus soft non-gating character, never an inferred behavioral verdict, and is the citable `Stated memory` tier that yields to this run's `DerivedMetric` on conflict.
A `StravaImport` is the resumable-state row for one historical import: `since_date`, `status`, `cursor_page`, `activities_imported`, and a nullable `error`.
A `UserMaterial` (ADR 0017) is one runner-uploaded markdown coaching material, the product's first untrusted-input surface, scoped per `user_id`.
It stores the untrusted `raw_text` (never placed into a prompt, never echoed over the API), a `distilled` corpus-`School`-shaped record, a `status` lifecycle (`processing`, `active`, `failed`, `archived`), a `content_hash` for dedup, and `distill_model`/`distilled_at`.
A `GoalRace` is the runner's own stated race: `name`, `race_date`, `distance_m`, and an `A`/`B`/`C` `priority` that is the runner's ranking and never a claim about ability.
A `TrainingPlan` is the plan container: a nullable `goal_race_id`, a `horizon_end`, and two strict-coerced JSON columns `rules` (`List[SpacingRule]`) and `week_shapes` (`List[PlannedWeekShape]`).
Its `status` is `drafting`, `active`, `superseded`, or `failed`, with at most one active plan per user held by the writer rather than a DB constraint.
`superseded_at` records when a plan stopped being current, written only by `activate_plan` and cleared on the row it activates, so a superseded plan stays reachable and restorable.
A `PlannedSession` is the schedule's concrete unit, described along three independent axes: PLACEMENT, COMMITMENT (`committed` or `suggested`), and DISCIPLINE (`run`, `walk`, `bike`, `strength`, `row`, `other`).
Placement has no column: a session stores an inclusive `[window_start, window_end]`, and `derive_placement` reads `pinned`, `week`, or `window` from its span.
The EFFECTIVE window is `max(window_start, today)..window_end`, computed at read time by `effective_window`, so a floating session narrows with no job running and the stored window never moves.
`PlannedSession.structure` is `{reps_planned, rep_distance_m, rest_s}`, and completion columns are `completed_at`, `completed_activity_id`, `completion_source`, and `dismissed_at`.
`intent` carries the session's reading and is orthogonal to discipline, since an easy bike and an easy run are the same stimulus.
A `PeriodReport` is a runner-requested review over a chosen `period_start`/`period_end` and discipline set, with a `generating`/`ready`/`failed` status.

## Scope
The backend exposes JSON endpoints under `/api` for health, Strava OAuth, profile read and update, activity listing and detail, sync, deep processing, stream backfill, bulk re-analysis, intent labelling, check-ins, trends, and account deletion.
`POST /api/activities/refresh` enqueues a bounded per-user check for webhook-missed activities (ADR 0006 self-heal).
`GET /api/trends/load` returns chronological weekly load scores, a trailing-4-week optimal band, a day-of-week breakdown, and per-activity contributions.
`GET /api/trends/volume?range=` returns per-metric current-vs-norm for the selected range, as of today unless an `as_of` date is given.
`POST /api/activities/{id}/coach-report/regenerate` enqueues `regenerate_report_job` and returns 202; the frontend polls the GET endpoint for a newer `generated_at`.
Coach threads live under `/api/coach/threads`: list, read, rename, and delete, plus the SSE turn `POST /api/coach/threads/messages` and `POST /api/coach/threads/actions/confirm`.
`GET`/`PUT /api/coach/voice` and `GET`/`PUT /api/coach/stance` read and write the runner's declared Voice and Stance plus the catalogs the profile UI renders from.
Smaller reads round out the surface: `GET /api/activities/earliest-date`, `GET /api/stats/weekly`, `GET /api/auth/strava/status`, `GET`/`DELETE /api/activities/{id}/coach-chat`, and `GET /api/coach/telegram/link-status` with `DELETE /api/coach/telegram/link`.
`GET /api/coach/feature-flags` reports the enabled-state of every coach input with a UI surface, plus `threads`, and is deliberately not itself gated.
`POST /api/blocks/{id}/split` and `POST /api/blocks/{id}/merge` are the block corrections; both set `user_corrected`, recompute bounds and primary, and inherit exchange sentinels so nothing re-fires.
Period reports add `POST`/`GET /api/coach/period-reports` and `GET /api/coach/period-reports/{id}`, all behind the `COACH_PERIOD_REPORT_ENABLED` router kill switch.
Coach materials add `POST`/`GET /api/coach/materials`, `GET /api/coach/materials/{id}`, `POST /api/coach/materials/{id}/archive`, and `DELETE /api/coach/materials/{id}`.
`POST /api/strava/import` starts a resumable walk of Strava history from a chosen `since_date`, and `GET /api/strava/import/status` is the progress poll.
The import takes raw data only: activity summaries plus deterministic analysis, never streams, never a coach report, and never a notification.
`DELETE /api/account` removes the Clerk user first, then deletes every row the user owns; a failed Clerk removal touches nothing locally and returns 502.
The schedule exposes `GET /api/schedule/week`, `GET /api/schedule/horizon`, `GET`/`POST /api/schedule/races`, and `DELETE /api/schedule/races/{race_id}`.
`POST /api/schedule/draft` asks the coach to draft a plan, creating a `drafting` row and enqueueing `generate_schedule_job`; `GET /api/schedule/draft` is the status poll.
`GET /api/schedule/plans/previous` reports the plan the runner trained to before this one, and `POST /api/schedule/plans/{plan_id}/restore` brings it back.
`POST`/`DELETE /api/schedule/sessions/{session_id}/complete` tick and untick a session by hand, and `POST /api/schedule/sessions/{session_id}/dismiss` declines a suggestion only.
There is no session-create endpoint: every `PlannedSession` is written by the coach's draft, not a form.
The whole schedule router sits behind the `SCHEDULE_ENABLED` router kill switch.
Strava ingestion supports manual sync (`POST /api/sync`) and incoming webhooks (`/api/webhooks/strava`).
Webhook `create` events enqueue `process_new_activity_job`, `update` events enqueue `sync_activity_job`, and `delete` events soft-delete the activity and detach it from its Block.
The webhook-create enqueue, the self-heal enqueue, and report regeneration each attach the bounded `PIPELINE_RETRY` policy (3 attempts at 60/300/900s); the `update` path does not.
`POST /api/sync` takes `since_days` (default 30) and imports summaries only, never fetching streams in-request; it hands stream fetching to the budget-gated background backfill chain.
Every `ingest_recent_activities` run refreshes the runner's Strava HR zones onto their `UserProfile` via `sync_athlete_zones`, once per sync.
`POST /api/activities/backfill-streams` enqueues a self-pacing job that fetches streams and re-runs analysis for activities lacking them, marking each via `Activity.streams_backfilled_at`.
`POST /api/activities/reanalyze` enqueues a self-pacing job that re-runs `analyze` from already-stored streams, cursor-paged by `strava_activity_id`, with no Strava calls and no notifications.
Both maintenance jobs are scoped to the triggering runner via a `user_id` job arg under a per-user job id, and schedule their successor with `queue.enqueue_in`.
Strava webhook events are authenticated before any side effect by `_event_is_authentic`: `owner_id` must match a connected `StravaAccount`, and `subscription_id` must match `STRAVA_WEBHOOK_SUBSCRIPTION_ID` when non-zero.
Inbound Telegram updates POST to `/api/webhooks/telegram`, authenticated by the outer `X-Telegram-Bot-Api-Secret-Token` header matching `TELEGRAM_WEBHOOK_SECRET`, failing closed in production when unset.
The inner Telegram authorization resolves the tap's `chat.id` to an acting `User` and requires that user to own the tapped activity; an unbound chat, an unknown chat, or a cross-user tap silently 200-acks with no write.
A `/start <token>` message binds the sending chat to the signed-in user who minted the one-time token, and `POST /api/coach/telegram/link-token` mints the deep link.
An authentic RPE or pain tap writes the same `CheckIn` the in-app endpoint writes, re-analyzes the activity, check-marks the tapped button, and answers with an ephemeral toast.
Under the receipt cadence the keyboard also carries a `done` button that records `Exchange.done_at` and schedules the full report, writing no `CheckIn`.
The frontend renders a home activity list, a per-activity detail page with charts and panels, a profile page, a trends page, a training-load page at `/load`, a schedule page at `/schedule`, and period reports at `/period-reports`.
The activity page has no chat box; its conversational question options open the coach sheet on that run's thread with the question prefilled.
The backend authenticates each request with a Clerk session JWT verified against Clerk's JWKS, resolving the user by verified email (ADR 0022).
`require_current_user` is the per-user scoping anchor applied to every application router, and the user is got-or-created on first authenticated request.
`BasicAuthMiddleware` is the frontend-to-backend service secret, not the user gate, and exempts `/api/health`, `/api/webhooks`, and `/api/auth/strava/callback`.
Auth degrades to a single local user only when Clerk is unconfigured outside production; in production a missing Clerk config fails closed with 503, and the web process refuses to boot when `CLERK_JWKS_URL`, `BASIC_AUTH_USER`, or `BASIC_AUTH_PASSWORD` is unset.
`/api/auth/strava/login` is gated on the session and mints a short-lived HMAC-signed `state` carrying the authenticated `user_id`, which the bare-redirect callback verifies to link the new `StravaAccount`.
Railway runs the backend as two services off one image (`web` FastAPI, `worker` RQ with `with_scheduler=True`) plus managed Postgres and Redis; Vercel hosts the frontend.
Server components call `BACKEND_URL` directly and the catch-all route handler `frontend/app/api/[...path]/route.ts` proxies client-side `/api/*` calls, both injecting HTTP Basic credentials server-side.
Locally `docker compose` provides only Postgres and Redis, while the host runs `uvicorn`, `rq worker --with-scheduler`, and `next dev`.
No production hostname is hardcoded; every seam URL is an env var, so a custom domain is a config and DNS change.

## Important Constraints
Settings come from `backend/.env` via `pydantic-settings`; `DATABASE_URL` is required and the app will not boot without it.
Anthropic access requires `ANTHROPIC_API_KEY`, with the coach model and prompt configured by `COACH_MODEL_ID` and `COACH_PROMPT_ID`.
Three model lanes fall back to `COACH_MODEL_ID` when unset: `COACH_CHAT_MODEL_ID` (conversational turns), `COACH_VOICE_MODEL_ID` (the voice rewrite), and `COACH_PERIOD_MODEL_ID` (a period report).
The code default prompt is `coach_message_v8`, while `backend/.env.example`'s prod-parity block declares `coach_message_lean_grouped_v11`; every earlier `coach_message_lean_grouped_*` id stays registered, so a rollback is a pure config flip.
Selecting a prompt is a pure `COACH_PROMPT_ID` config flip with no code change, and the versioned cache identity retains reports generated under prior prompt ids.
A prompt whose `PROMPT_FEATURES` entry carries `TWO_STAGE` activates the two-stage Exchange; any single-shot id serves the prior path with zero code change.
`COACH_RECEIPT_CADENCE` (bool, default off, ADR 0018) is orthogonal to `COACH_PROMPT_ID` and is ON in production.
When on and the active prompt is two-stage, it replaces the debounced LLM opener and 3h fuller timer with an instant deterministic per-activity receipt plus one full report about `BLOCK_GAP_SECONDS` after the session; it is inert under a single-shot prompt.
Eighteen `COACH_*_ENABLED` bools exist; most REMOVE one named item from what the coach receives, while `COACH_THREADS_ENABLED` and `COACH_PERIOD_REPORT_ENABLED` gate a surface instead.
`COACH_MEMORY_ENABLED` drops the `memory` pack section and disables the runner-memory update writer.
`COACH_VOICE_BLOCK_ENABLED` off means the voice rewrite pass never runs, so every runner reads the voiceless baseline.
`COACH_THREADS_ENABLED` off means every `/api/coach/threads` route refuses with 503 and the frontend renders no launcher, sheet, or conversational report options.
It hides the conversation without deleting it: stored threads, activity-scoped chat history, the report pipeline, receipts, and notifications are untouched, and a refused turn writes no row.
PRODUCTION DOES NOT RUN THE CODE DEFAULTS, and the difference is what the coach actually receives, so any reasoning about its inputs starts here rather than from the defaults above.
Eleven coach inputs are OFF in the deployed environment: `COACH_ADHERENCE_ENABLED`, `COACH_CONTINUITY_ENABLED`, `COACH_HOUSE_SCHOOLS_ENABLED`, `COACH_LONGITUDINAL_ENABLED`, `COACH_PLAYBOOK_ENABLED`, `COACH_PREVIOUS_30D_ENABLED`, `COACH_PRIOR_REPORTS_ENABLED`, `COACH_SALIENCE_ENABLED`, `COACH_SLEEP_QUALITY_ENABLED`, `COACH_STOPS_ANALYSIS_ENABLED`, `COACH_USER_MATERIALS_ENABLED`.
Every other `COACH_*_ENABLED` is on.
`backend/.env.example`'s prod-parity block is the source of truth for that list and is what `make diagram-check` pins the diagrams against.
A switch that block does not declare runs at its CODE default, which is False for `COACH_ADHERENCE_ENABLED` and `COACH_PRIOR_REPORTS_ENABLED` and True for the rest, so "absent" never means "on".
`backend/tests/test_prod_switch_state_919.py` recomputes that list the way the app resolves it and fails the build when this file drifts from it.
`SCHEDULE_ENABLED` (default True) gates the schedule screen: off, every `/api/schedule` route refuses with 503 and the frontend renders no Schedule tab, while stored plans, sessions, and races are untouched.
The orthogonal coach-input switch `COACH_SCHEDULE_ENABLED` (default True) drops the `right_now.schedule` pack section while the schedule screen keeps working.
`SCHEDULE_HORIZON_WEEKS` (default 12) and `SCHEDULE_CONCRETE_WEEKS` (default 3) are inputs to the drafting prompt as well as the horizon read.
`COACH_PERIOD_REPORT_ENABLED` (default True) gates every `/api/coach/period-reports` route with 503 and hides the frontend entry point.
`EXCHANGE_STAGE2_DELAY_SECONDS` (default 10800) is the fuller-turn timer and `EXCHANGE_REPLY_WINDOW_SECONDS` (default 86400) is how long a reply still triggers the fuller turn early; both are inert under a single-shot prompt.
`RQ_JOB_TIMEOUT_SECONDS` (default 600) is the RQ death-penalty ceiling, applied as the queue `default_timeout` and as explicit `job_timeout=` on `queue.enqueue_in` calls, because a two-stage generation runs past RQ's 180s default.
`BLOCK_GAP_SECONDS` (default 1800) is both the block grouping threshold and the block-complete debounce that gates the opener.
Block assignment runs under every prompt, while the block-complete opener trigger is two-stage-only.
Telegram is the only notification channel, active when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are both set, otherwise the notifier is a no-op.
`resolve_recipient(user)` returns the activity owner's bound `User.telegram_chat_id`; an unbound user falls back to the global `TELEGRAM_CHAT_ID` only for the identified deployment owner (`OWNER_EMAIL`, or a db-proven single-user deploy) and otherwise fails closed to null.
The Telegram vars must be set on both Railway app services, with `TELEGRAM_WEBHOOK_SECRET` and `TELEGRAM_BOT_USERNAME` needed on web only.
`SELF_HEAL_MIN_INTERVAL_SECONDS` (default 60) bounds the per-user Strava check via an atomic Redis key, and the check looks back a fixed 7-day window.
The self-pacing jobs are paced by `BACKFILL_BATCH_SIZE`/`BACKFILL_BATCH_PAUSE_SECONDS` (20/300, keeping the stream backfill under Strava's 100-requests/15-min ceiling), `IMPORT_PAGE_SIZE`/`IMPORT_BATCH_PAUSE_SECONDS` (50/5), and `REANALYZE_BATCH_SIZE`/`REANALYZE_BATCH_PAUSE_SECONDS` (100/5, local compute only).
`CORS_ALLOWED_ORIGINS` (comma-separated, default `http://localhost:3000,http://localhost:8000`) is parsed by `Settings.cors_allowed_origins_list`.
Error tracking is logs-only by default; Sentry capture requires the `observability` extra and `SENTRY_DSN`, otherwise `init_sentry` is a no-op.
Platform training-load numbers (Strava Fitness/Freshness, Garmin Training Load) are validation-only: never authoritative, and never a cold-start seed for our own readiness model, because they are a different unit.
A stored `context_pack` is expected to strict-parse under the current `CoachContextPack` unless its prompt id is in `UNREADABLE_PACK_PROMPT_IDS` (ADR 0032).
Every site re-parsing a stored pack goes through `load_stored_pack`, which raises `StoredPackUnreadable` past that cutoff and re-raises the underlying `ValidationError` otherwise; no stored pack is ever migrated or deleted.
Postgres is exposed on host port `5433`, Redis on `6379`, backend on `8000`, and frontend on `3000`.

## Architecture Summary
The backend is a FastAPI app (`app/main.py`) wiring routers from `app/api/*` under the `/api` prefix.
Persistence uses SQLAlchemy 2.x ORM with Postgres, with one ORM model per file under `backend/app/models/` and migrations under `backend/alembic/versions/`.
Background work runs via Redis-backed RQ, with jobs in `app/jobs/` and the entrypoint `python -m app.worker` running `worker.work(with_scheduler=True)`.
There is no separate scheduler process: the worker's embedded scheduler thread drains both interval retries and every deferred `queue.enqueue_in` job.
The Strava integration is a port (`StravaPort`) with `HTTPStravaAdapter` and `InMemoryStravaAdapter`, split into `ingestion.py`, `persistence.py`, `auth.py`, and `zone_sync.py`.
Analysis is a pipeline of pure-ish functions in `app/services/analysis/` composed by `_orchestrator.py`; the public surface is `analyze` and `analyze_with_streams`.
`stages.py` holds the `ANALYSIS_STAGES` registry of thirteen `Stage` descriptors, each declaring what it READS and WRITES, with `assert_stage_contract` running at import so a stage reading an unwritten field is a startup `RuntimeError`.
The `DerivedMetric` upsert writes all 23 columns unconditionally, so a stage that abstains overwrites the prior value.
The interval stage has two sources behind one `interval_structure` contract: `detect_intervals_from_laps` reads the runner's recorded Strava laps and takes precedence on a clear bimodal pattern, tagged `source="recorded_laps"`.
The coach layer chains `context.py`, `llm.py`, the Pydantic schemas in `app/schemas/coach.py`, `validator.py`, `service.py`, and finally `voice_rewrite.py`.
`service.py` dispatches on prompt family to `_generate_structured` or `_generate_message`, caches the result in `CoachReport`, and sets `is_fallback=True` on LLM or parse failure.
The prose path degrades rather than withholds: a real message survives a missing tail as `tail_degraded=True`, while a medical-overreach violation surviving the policy retry forces `is_fallback=True`.
The report is generated VOICELESS, and `voice_rewrite.py` says the finished prose again in the runner's declared voice as the last stage.
The rewrite may re-word and re-emphasise but may not introduce a fact, drop a safety item, or change a verdict, so voice has no route to the substance.
The voiceless baseline stays in `message`/`opener_message` for the digest, eval harness, and learning loop, and the voiced rendering lands in `voiced_message`/`voiced_opener_message` for the runner.
Every rewrite failure resolves to serving the baseline, so a style pass can never cost the runner their coaching.
The deterministic policy validator has seven rules, numbered 1-8 with 6 skipped, and this gate must not be bypassed.
It rejects output that misses questions for a null check-in, references uncalibrated HR zones, cites a risk flag absent from the flags array, or claims specific interval execution counts under low detection confidence.
Rule 5 is the medical-scope floor: no dose advice, no diagnosis verbs, no directive medication advice, and no asserted clinical condition, while still permitting interpretive metric correction and a non-diagnostic referral nudge.
Rules 7 and 8 reject citing a `corpus.*` or `corpus.user_materials.*` field path as report evidence; the runner memory profile deliberately gets no such rule because it is the grounded, citable tier.
The seven rule bodies are shared `check_*` functions assembled three ways: `validate_policy` for the structured shape, `validate_message_policy` for prose, and `validate_conversational_policy` for streamed replies (ADR 0024).
A medical overreach withholds the raw reply and serves `MEDICAL_REDIRECT_MESSAGE`; soft violations are logged and let through.
`services/coach/signal_registry.py` is the ONE declaration every derived view reads: one frozen `CoachSignal` row per flat pack section carrying its group, flat position, `PromptFeature` gate, drop and nested trim, kill switches with effect and application site, read-time adapters, and either a switch or a recorded `ungated_reason`.
`coach_context.py` derives `_SECTION_GROUP`, `_FLAT_ORDER`, and `PACK_SECTIONS` from it, and `context.py` derives its `ReadTimeSignal` objects from it, registering only the `compute`.
Under a `GROUPED_PACK` prompt the same content is re-nested into five coaching-question groups (`this_run`, `right_now`, `the_runner`, `our_thread`, `how_to_coach`) plus top-level `safety_rules`, via `pack.to_grouped_dict`.
The flat pack sections, by group, are `this_run` (`activity`, `metrics`, `check_in`, `perceived_effort`, `calibration`, `block`, `stream_view`, `intensity`, `intensity_read`, `referral`), `right_now` (`training_load`, `training_volume`, `recent_training`, `readiness`, `recent_weeks`, `intensity_mix`, `schedule`), `the_runner` (`profile`, `training_history`, `memory`), `our_thread` (`longitudinal`, `adherence`, `continuity`), `how_to_coach` (`corpus`, `stance`), plus top-level `salience`.
`recent_training_summary`, `believed_facts`, `preference_profile`, and `narrative` are never-populated Optional stubs retained so older stored packs still validate under the pack's `extra="forbid"`.
The outgoing LLM message is a one-way view built by `coach_framing.coach_llm_view`, shared by the report and chat seams so both LLMs read an identical pack.
`prompts.py` is the prompt registry and assembly, `prompt_clauses.py` owns the live grouped lineage's text as named clauses plus `compose`, and `prompt_archive.py` holds every retired prompt string verbatim.
The safety floor is a clause marked `is_floor` in the spine every version is built from, no version declaration has a slot that could drop it, and `compose` raises `MissingSafetyFloorError` on a clause set carrying none.
`prompt_features.PROMPT_FEATURES` is the capability manifest, one frozenset of `PromptFeature` per prompt id, from which every capability view and the grouped-pack id set derive.
A pack section is emitted only under a prompt whose manifest entry carries its feature and drops byte-stably otherwise, so a rollback changes what the coach receives without disturbing any other prompt's pack.
Adding a prompt version is two declaration rows (`PROMPT_FEATURES` and `PROSE_VARIANTS`) plus its own test, editing no prior version's test.
Under a `TWO_STAGE` prompt, `build_system_prompt(prompt_id, mode="opener"|"fuller")` serves two modes selected by the caller, both sharing one cache identity and one evolving `coach_reports` row.
`report_offer.py` owns the report's own reach into the offer-and-confirm mechanism: `REPORT_OFFER_KINDS` is `adjust_session` alone, the offer is stored without a token, and `mint_report_offer` mints the single-use token when the owner READS the report.
Screen context (ADR 0028) makes the thread turn screen-aware as a SERVER-RESOLVED POINTER: the client sends a `ScreenPointer` of screen key plus view selections only, typed with `extra="forbid"` so a fact can never travel from the client.
Coaching skills (ADR 0029) are code-resident, house-authored PROCEDURES disclosed progressively: the system prompt carries each skill's name and one-line trigger, and the model pulls the full procedure with the `load_coaching_skill` tool.
The retrieval seam (`retrieval.py`) pulls stored processed artifacts on demand: the deferred stream view, prior exchange digests, prior structured commitments, and the keyed coaching corpus including the runner's active distilled materials.
Durable memory is rewrite-from-source (ADR 0025): `gather_memory_sources` bundles the runner's own notes and chat plus a bounded digest window, and the writer rebuilds the whole profile without ever reading its stored value, so the anti-echo guarantee is a property of input construction.
That containment spine — structured output only, strict coercion, raw text never entering a prompt — is how the first untrusted-input surface is made safe (ADR 0017), and the schedule draft and period report reuse it.
`services/blocks.py` owns BLOCK shape only and routes every `Exchange` row it touches through `coach/exchange_lifecycle.py`, the single owner of every legal `Exchange` transition and the at-most-once notification invariant.
The split is by transaction posture: state transitions commit, structural changes join the caller's transaction.
`services/activity_facts.py` is the single home of the FACT STREAM and every question asked of it, so the coach pack and the Trends page cannot disagree about the same number.
`scan`/`scan_cache` is the block-scoped memo that lets one coach pack build answer its five overlapping history windows from one fetch per session shape.
`services/weeks.py` is the single week-boundary definition, parameterized by the runner's `week_starts_on` and shared by the coach pack and the Trends services.
The webhook and self-heal convergence is `app/jobs/process_new_activity.py` (ADR 0004): ingest, analyze, assign block, then dispatch to the active cadence.
The per-event cadence dispatch sits behind the swappable `app/jobs/cadence/` seam (ADR 0019), one module per cadence, resolved at fire time by `get_active_cadence(settings)` so a config-flip rollback is preserved.
`app/jobs/exchange_ops.py` holds the cadence-agnostic mechanics, and the import direction is one-way from cadence to exchange_ops.
Data flow: Strava API, `strava_ingestion`, `Activity`/`ActivityStream` rows, the analysis pipeline, `DerivedMetric`, the coach service, `CoachReport`, then either the frontend read endpoint or a Telegram notification.

## Key Dependencies
`fastapi`, `uvicorn`: HTTP server and ASGI runtime for the backend.
`sqlalchemy`, `psycopg`, `alembic`: ORM, Postgres driver, and schema migrations.
`pydantic`, `pydantic-settings`: request/response schemas and environment configuration.
`pyjwt`: verifies the Clerk session JWT against Clerk's JWKS.
`httpx`: outbound HTTP client used for Strava and Anthropic calls.
`redis`, `rq`: job queue for sync and processing background work.
`numpy`: numerical computation in the processing pipeline.
`anthropic`: Claude API client used by the coach service, pinned `>=0.125.0,<0.126.0` because 1.0.0 removed `temperature`/`top_p`/`top_k` from `messages.create()` and `.stream()` and neither takes `**kwargs`.
`python-multipart`: form parsing required by FastAPI for non-JSON request bodies.
`sentry-sdk[fastapi]` (optional `observability` extra): error tracking, installed only when Sentry capture is enabled.
`pytest`, `pytest-asyncio` (test extra): test runner and async test support.
`next`, `react`, `react-dom`: frontend framework and renderer.
`@clerk/nextjs`: social-login authentication and the frontend session gate.
`recharts`: charting library for stream and trend views.
`react-markdown`, `remark-gfm`: render the coach report body as GitHub-flavoured markdown.
`date-fns`: date formatting in activity and trends views.
`lucide-react`: icon set used across the UI.
`next-themes`: dark/light/system theme switching with persistence and no flash on load.
`tailwindcss`, `@tailwindcss/typography`, `autoprefixer`, `postcss`: styling pipeline.
`typescript`, `eslint`, `eslint-config-next`: type checking and lint baseline.

## Project Structure
`backend/app/main.py` boots the FastAPI app and registers routers.
`backend/app/api/` holds one router per resource: `health`, `auth`, `profile`, `activities`, `blocks`, `webhooks`, `trends`, `coach`, `materials`, `strava_import`, `threads`, `account`, `schedule`, `period_reports`, `debug`.
`webhooks.py` hosts both the Strava event webhook and the Telegram inbound callback.
`backend/app/api/deps.py` is the single home of owner-scoped route dependencies, so tenant scoping is a property of the ROUTE rather than a convention repeated in handler bodies.
A handler declares the owned resource it operates on (`OwnedActivity`, `OwnedBlock`, `OwnedThread`, `OwnedMaterial`, `OwnedGoalRace`, `OwnedPlannedSession`, and siblings) and resolution runs before the body.
`backend/app/core/` holds `config.py` (the typed settings object), `queue.py` (RQ setup), `clerk_auth.py`, `oauth_state.py`, and `observability.py`.
`backend/app/schemas/` holds Pydantic request and response schemas, one file per domain.
`backend/app/services/strava_ingestion/` holds the Strava port and adapters, the batch orchestrators, the persistence writer, token-refresh auth, and ingestion-time HR-zone calibration.
`backend/app/services/analysis/` is the metrics pipeline: `_orchestrator.py` composes the stage modules, `stages.py` is the stage contract, and `composition.py` holds the typed intermediates.
`backend/app/services/coach/` owns the LLM coach; each module has one job and the module map is its `__init__.py`.
`turn.py` is the coaching-turn envelope shared by all generation paths: the `TurnKind` lane, `resolve_model`, `build_client` returning a spend-recording `MeteredClient`, the `over_budget` gate, and `relationship_for_user`.
`chat.py`, `threads.py`, `thread_turn.py`, `proposed_actions.py`, `screen_context.py`, `coaching_skills.py`, and `query_tools.py` are the conversational surface.
`voice.py`, `stance.py`, and `corpus.py` are pure domains with no LLM and no I/O; `voice_rewrite.py`, `material_distiller.py`, `receipt.py`, and `receipt_voice.py` are their generative counterparts.
`perceived_effort.py`, `adherence.py`, `calibration.py`, `volume.py`, `salience.py`, `intensity.py`, and `recent_training.py` are the pure read-time signal builders.
`memory_store.py` and `memory_update.py` are the runner-memory DB layer and its rewrite-from-source writer.
`period_report_pack.py`, `period_report.py`, and `period_report_store.py` are the period-report surface.
`eval/` is the offline eval harness: `rubric.py`, `harness.py`, and `fixtures.py`.
`backend/app/services/schedule/` is the schedule package: `disciplines.py`, `placement.py`, `rules.py`, `store.py`, `norms.py`, `week.py`, `horizon.py`, `draft.py`, `draft_contract.py`, `plan_validator.py`, `effort.py`, `completion.py`, and `coach_view.py`.
The package computes no training total of its own: actuals and windows come from `activity_facts`, the week boundary from `weeks.py`, and typical from `coach/volume.py` and its own `norms.py`.
`backend/app/services/notifications/` holds the notifier port and adapters, the channel selection and composer, the Telegram template, the shared prose-render helpers, and the opaque tap-token codec.
`backend/app/services/` also holds `blocks.py`, `weeks.py`, `activity_facts.py`, `trends.py`, `training_load.py`, `readiness.py`, `laps.py`, `activity_queries.py`, `account_deletion.py`, `checkins.py`, `intents.py`, and `units/cadence.py`.
`checkins.py` and `intents.py` are shared single write paths used by both the API and the Telegram or proposed-action callers.
`intents.py` is also the single home of the stated-intent vocabulary, rendered by the frontend from `ActivityDetailRead.intent_options` rather than a frontend copy.
`backend/app/jobs/` holds the RQ jobs, with `process_new_activity.py` as the convergence pipeline and the job layer's four entrypoints.
Those four entrypoints must keep this module path, because RQ serializes a deferred job as its `module.function` string.
`backend/app/jobs/cadence/` is the post-activity cadence seam, and `batch_chain.py` is the shared self-pacing batch-chain module the maintenance jobs are built from.
`backend/scripts/` holds `pre_deploy.py` (the Railway pre-deploy entrypoint: the env preflight, then `alembic upgrade head` when `RUN_MIGRATIONS=true`, set on the `web` service only), `seed_from_prod.py`, `reanalyze_all.py`, `eval_coach_reports.py`, and `post_deploy_verify.py`.
`frontend/app/` holds the routes: `page.tsx` (home), `activity/[id]`, `profile`, `trends`, `load`, `schedule`, `period-reports`, and the catch-all API proxy `api/[...path]/route.ts`.
`frontend/app/manifest.ts` and `apple-icon.tsx` are the PWA install surface, and `middleware.ts` excludes `apple-icon` by name because that path carries no dot and would otherwise sit inside the Clerk gate.
`frontend/components/` holds the activity panels, a `trends/` subfolder, a `load/` subfolder, a `coach/` subfolder (the sheet provider, sheet, thread switcher, launcher), and a `schedule/` subfolder.
`frontend/lib/` holds `api.ts` (`fetchFromAPI`), `format.ts`, `useKeyboardOpen.ts`, `coachStream.ts`, and the `types/` domain types with a `types.ts` barrel.
`docs/adr/` holds the architecture decision records, and `docs/diagrams/` holds the generated coach-pack and coach-chat flow diagrams with their drift guard.
`Makefile` exposes `smoke`, `test`, `backend-test`, `frontend-test`, `seed-local`, `eval`, `eval-selftest`, `alembic-check`, `diagram-check`, `verify-local`, `deployed-handshake-smoke`, and `post-deploy-verify`.

## Testing Overview
Backend tests run via `python -m pytest`; the baseline command is `make backend-test`, which excludes tests marked `integration`.
The test session deliberately opts out of `backend/.env`: `tests/conftest.py` sets `RUNNING_COACH_SKIP_DOTENV` before importing the app, so a local run resolves exactly the code defaults CI resolves.
`tests/test_settings_isolation.py` guards both halves, matching `COACH_*` settings by prefix so the growing kill-switch family needs no list maintenance.
The opt-out covers the env FILE only; a variable exported in the developer's shell still wins.
Backend unit and policy coverage exists for analysis, intervals, the policy validator, the coach context and schema, the two-stage exchange, blocks, the reply path, self-heal, webhooks, the end-to-end pipeline, models, playbooks, risk, Strava auth, stream metrics, sync integration, units, workout matching, and the schedule package.
Structural route sweeps walk the route table through one shared enumeration, `backend/tests/_route_table.py`, because a sweep over an empty enumeration passes silently rather than erroring.
`assert_enumeration_is_not_vacuous` proves the enumeration against the app's own OpenAPI document plus a hard route-count floor, and `tests/test_route_table.py` is the guard on that guard.
`tests/test_route_ownership_802.py` fails when any route taking an owned-resource path parameter does not resolve it through `deps.py`.
`tests/test_context_budget_907.py` fails when this file exceeds its declared line and character budget.
`tests/test_anthropic_pin_966.py` asserts the installed `anthropic` version satisfies the declared line and that the line still excludes 1.0.0.
Frontend regression runs via `npm run test`, which invokes `next lint` then `next build`; there is no Jest or component test runner.
Frontend smoke runs via `npm run smoke` (`frontend/scripts/smoke.mjs`), booting a mock API and a Next dev server on dynamically chosen free ports and verifying core routes load.
`make alembic-check` brings a throwaway Postgres to head then runs `alembic check`, catching the model/migration drift `make backend-test` is structurally blind to because the suite builds its schema with `create_all`.
The eval harness scores each report from its stored `report` and `context_pack` against sixteen rubric assertions, aggregates a scorecard scoped to the current `(prompt_id, schema_version)`, and flags regressions across versions.
It scores the fuller turn only: opener-only rows are skipped and counted, never scored.
`make diagram-check` guards both generated diagrams against the declarations they were produced from, covering pack sections, `DerivedMetric` coverage, kill-switch and prompt parity, the nested pack key set, generator call signatures, and the chat turn's tools, skills, action kinds, screen keys, and prompt slots.
`backend/tests/test_diagram_drift.py` tests that wiring itself, because every comparison is a pure function that would stay green if the guard simply stopped calling it.
CI runs `.github/workflows/deploy.yml` on push and pull requests to `main`, with `backend-test`, `frontend-test`, `alembic-check`, and a push-only `post-deploy-verify` job.
Major gap: no automated frontend unit or component tests beyond build-time lint and smoke route checks.
Major gap: no end-to-end test that exercises a real Strava-to-coach-report flow.

## Real-Data Verification
There is no Strava OAuth application for dev or local, so the connect-then-sync path cannot bootstrap data outside production; both verification paths are documented in `docs/testing/local-seed.md`.
Path 1 (deployed): the production app is publicly reachable and renders real synced data, so browser automation against it verifies any UI or read-path change with no local setup.
Per-branch preview deployments are NOT isolated: they point at the same single production backend and database, so any write on a preview mutates production data and they are read-mostly.
Path 2 (local): `make seed-local` runs `backend/scripts/seed_from_prod.py`, which resets the local schema to the branch's migration head and copies a production snapshot so the analyze-coach-frontend chain runs offline against real data.
`SEED_ARGS="--activities N"` bounds the snapshot, a table the source does not have yet arrives empty, and `seed_from_prod.py` only ever reads from the source and refuses any target URL that is not `localhost` or `127.0.0.1`.
The seed redacts Strava tokens by default so local cannot call Strava or rotate the production refresh token, which means "Sync Now" fails on a seeded local DB; `SEED_ARGS="--with-live-tokens"` is the opt-in.
`make verify-local` is the supported browser path: it runs the backend with `LOCAL_NO_AUTH=true` and the frontend with `NEXT_PUBLIC_LOCAL_NO_AUTH=true`, since an agent cannot complete a Clerk sign-in.
Both fail closed in production, so this is no production bypass, and the target does not start the rq worker.
The two deployed-only handshakes that cannot be bootstrapped locally (Strava OAuth `state`, Telegram bind and tap auth) have a manual runbook at `docs/testing/deployed-handshake-verification.md`.

## Maintenance Checklist
Write exactly one sentence per line, and never grow an existing line to carry a new fact.
Keep this file under the budget `backend/tests/test_context_budget_907.py` enforces; when it is close, drop low-value detail before adding new content.
State what exists, where it lives, and what it is for; the code states how it works.
Omit rationale, precedent, and history unless a reader would act wrongly without it.
Record implementation facts only, and exclude issue and pull-request references.
Invoke the `aiw-project-context-management` skill before editing this file.
Update this file when a new top-level path is added under `backend/app/`, `frontend/app/`, `frontend/components/`, or `frontend/lib/`, or when a new SQLAlchemy model, table-adding migration, or API router is introduced.
Update this file when a direct dependency is added or removed in `backend/pyproject.toml` or `frontend/package.json`.
Update this file when the deterministic policy validator's rule set materially changes, or the Strava ingestion, processing, or coach pipeline gains or loses a stage.
Update this file when a `COACH_*_ENABLED` switch is added, when the production switch state changes, or when the local runtime topology (ports, services, env vars) changes.
Update this file when the schedule gains a surface or a write path, and update Real-Data Verification when the seeding script, its safety defaults, or the environment topology changes.
