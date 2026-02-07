# PROMPTS.md — Copilot Chat prompts (repo-anchored)

Use these prompts in VS Code Copilot Chat. Always start with “@workspace read SPEC.md and ARCHITECTURE.md”.

## Global instruction (paste first in new chats)
@workspace Read SPEC.md and ARCHITECTURE.md. Treat them as source of truth. Make small, reviewable changes. Before coding, list assumptions. Add tests where practical. Update docs if you change behavior.

---

## 1) Scaffold backend
@workspace Implement TODO Step 1. Create backend FastAPI app with:
- /api/health
- settings/config loader from env
- SQLAlchemy session setup
- alembic initialized
Only touch backend/ and README.md.

## 2) Database models
@workspace Implement TODO Step 2. Create SQLAlchemy models per ARCHITECTURE.md section 5:
users, strava_accounts, activities, derived_metrics, advice, user_profile, check_ins.
Also create Pydantic schemas for read/write.
Add an alembic migration.

## 3) Strava OAuth
@workspace Implement TODO Step 3. Add Strava OAuth endpoints:
GET /api/auth/strava/login and GET /api/auth/strava/callback.
Store tokens in strava_accounts. Add token refresh helper.
Document env vars in backend/.env.example.

## 4) Manual sync
@workspace Implement TODO Step 4. Add POST /api/sync to fetch the last 30 days activities for the connected athlete, store in activities.raw_summary and summary fields. Keep rate limits in mind and avoid unnecessary calls.

## 5) Webhooks
@workspace Implement TODO Step 5. Add Strava webhook verification and event receiver endpoints.
On activity create/update, enqueue sync_activity job.
On delete, mark activity is_deleted=true.

## 6) Worker queue
@workspace Implement TODO Step 6. Add Redis + RQ worker that can run:
- sync_recent_activities(user_id)
- sync_activity_by_strava_id(strava_activity_id)
Ensure jobs are idempotent.

## 7) Analysis engine
@workspace Implement TODO Step 7. Create services/analysis:
- classify_activity(activity, history)
- compute_metrics(activity, streams?)
- generate_flags(metrics, history, checkin?)
Write unit tests for classifier + flagging using synthetic activities.

## 8) Coaching engine
@workspace Implement TODO Step 8. Create services/coaching:
- generate_advice(activity, metrics, history, profile, checkin?)
Ensure advice follows SPEC.md structure exactly.
Store structured fields + full_text render.

## 9) Frontend scaffold
@workspace Implement TODO Step 9. Create Next.js frontend with:
- connect Strava button
- dashboard (week summary + activities)
- activity detail page with advice + check-in form
Use NEXT_PUBLIC_API_BASE_URL.

## 10) End-to-end test run
@workspace Implement TODO Step 10. Validate the end-to-end flow using real Strava data:
- connect Strava
- sync activities
- run analysis
- generate coaching


## 12) Profile v1 (persistent athlete context)
@workspace Read SPEC.md, ARCHITECTURE.md, TODO.md, and HANDOFF.md.
Implement TODO Step 12 only.
Backend:
- Ensure GET /api/profile and PUT /api/profile exist and match ARCHITECTURE.md endpoint conventions.
- Persist: injury_notes, experience_level, weekly_days_available, goal_type, target_date.
- Add: current_weekly_km (int) and upcoming_races (JSON list of {name, date, distance_km}).
Frontend:
- Add /profile page with a simple form to edit these fields.
Constraints:
- Minimal changes, no new auth system, no new deps.
- Add at least one backend test for profile update + retrieval.
Before coding: list files you will touch and why.


## 13) Strava real-data verification (sync + analysis + advice)
@workspace Read SPEC.md, ARCHITECTURE.md, TODO.md, and HANDOFF.md.
Implement TODO Step 13 only.
- Add a “Sync now” button to the dashboard that calls POST /api/sync and shows a structured result.
- Improve backend sync response to {fetched, upserted, skipped, analyzed, errors[]}.
- Ensure idempotency (no duplicates) and correct token refresh usage.
- Add logging for Strava API errors + missing scopes + rate limiting.
- Add one integration test that mocks Strava API calls and verifies:
  activities upserted and advice created.
Constraints:
- Do not redesign analysis/coaching logic. Focus on observability + reliability.
Before coding: list files you will touch and why.


## 14) AI augmentation v1 (commentary only, keep structure)
@workspace Read SPEC.md and current advice output format.
Implement TODO Step 14 only.
Backend:
- Add config flags: AI_ENABLED (default false), AI_PROVIDER, and provider key env var(s).
- Create services/ai with a client wrapper and prompt builder.
- Add AI commentary generation that uses activity + derived metrics + profile (+ check-ins if present).
- IMPORTANT: AI must not change the “Next run” prescription; it can only add commentary and one optional question.
- Persist AI output on the Advice record.
Frontend:
- Display AI commentary in a collapsible “Coach Notes (AI)” section on activity detail.
Testing:
- Mock AI client; no network calls.
Constraints:
- Minimal changes and safe defaults (AI off).
Before coding: list files you will touch and why.


## 15) Chat with Coach (activity-aware)
@workspace Read SPEC.md, ARCHITECTURE.md, TODO.md, and HANDOFF.md.
Implement TODO Step 15 only.
Backend:
- Add POST /api/chat accepting {message, activity_id?}.
- Assemble a context pack from DB:
  profile, selected activity summary, derived metrics, advice, and last 7–14 day summary.
- Use the AI client from Step 14.
- Responses must not hallucinate data not present in the context pack.
Frontend:
- Add a chat panel on /activity/[id] that sends messages with activity_id.
Testing:
- Add a backend test verifying context assembly and AI invocation (mock).
Constraints:
- Keep UI basic; do not add auth or complex conversation persistence unless necessary.
Before coding: list files you will touch and why.


## Step 16 — Replace Coach Verdict with AI-only report (remove separate AI panel)

Copilot prompt:

@workspace Read SPEC.md and the current Advice schema + UI rendering.
Implement Step 16: “Coach Verdict is fully AI-generated.”
Requirements:

Remove the separate “Coach Notes (AI)” panel from the UI.

The backend must generate the primary Advice fields using AI (verdict, evidence[], next_run, week_adjustment, warnings[], question).

AI must return strict JSON matching a Pydantic schema. Validate it. If invalid/unavailable, fall back to the existing rule-based generator.

Update the AI prompt to follow an expert coach breakdown: session type/purpose, execution, physiology (if present), technique proxies (if present), load context, next action, risk flags.

Tests: mock AI client; ensure valid JSON overrides rule-based, invalid JSON falls back.
Constraints: minimal changes, no new deps.

## Step 17 — Strava “rich data” ingestion (DetailedActivity + Streams)

Copilot prompt:

@workspace Implement Step 17: ingest richer Strava metrics.
Requirements:

Expand Strava activity fetch to store more fields from DetailedActivity (store full raw JSON already, but also persist key scalars used by analysis/UI).

Add optional stream fetching via GET /activities/{id}/streams for these 11 stream types when available: time, latlng, distance, altitude, velocity_smooth, heartrate, cadence, watts, temp, moving, grade_smooth.

Fetch streams selectively (runs only; and only if activity_class is hills/tempo/intervals/long OR user requests deep analysis).

Store streams efficiently (JSONB ok) and compute derived metrics from them (pace variability, hill reps detection, HR drift, cadence fade).

Respect rate limits and avoid repeated fetches.
Tests: mock stream responses; verify storage + derived metrics computed.
Constraints: no UI overhaul required, but ensure backend exposes derived metrics to the AI context pack.

---

## Debugging prompts
- “Find why token refresh isn’t happening before Strava calls; patch it and add a test.”
- “The classifier is mislabeling steady runs as tempo. Adjust heuristics and update tests.”
- “Advice is too verbose. Enforce SPEC.md structure and cap bullet counts.”



---

## Step 18a — Implement Coach Verdict v3 (structured “Run Report Template v3”)

## Prompt A — Define the ContextPack contract (backend + frontend + fixture)
TASK: Add a unified AI ContextPack contract (no behavior change yet).

In the backend, create a new Pydantic model named ContextPack (location: backend/app/services/ai/context_pack.py or equivalent). It must include these fields:

- activity: { id, start_time, type, name (optional), distance_m, moving_time_s, elapsed_time_s (optional), avg_pace_s_per_km (optional), avg_hr (optional), max_hr (optional), avg_cadence (optional), elevation_gain_m (optional) }
- athlete: { goal (optional), experience_level (optional), injury_notes (optional), age (optional), sex (optional) }  # include only if already available in DB/profile models
- derived_metrics: list of { key, value, unit (optional), confidence (0..1), evidence (string) }
- flags: list of { code, severity: "info"|"warn"|"risk", message, evidence (string) }
- last_7_days: { total_distance_m (optional), total_time_s (optional), intensity_summary (string), load_trend (string) }  # summary strings can be empty for now
- check_in: { rpe_0_10 (optional), pain_0_10 (optional), sleep_0_10 (optional), notes (optional) }
- available_signals: string[]
- missing_signals: string[]
- generated_at_iso: string

Rules:
- No business logic other than deterministic serialization.
- Add ContextPack.to_prompt_json() -> dict that outputs keys in stable order (deterministic).
- Add ContextPack.to_prompt_text() -> string that pretty-prints JSON with sorted keys.

Frontend: add a matching TS type (types.ts or types/ContextPack.ts) with the same shape.
Add a fixture JSON file (backend/tests/fixtures/context_pack_minimal.json) with a minimal valid example.

Finally: add a small unit test that loads the fixture and validates it against the Pydantic model.
Return code changes only. Do not modify existing prompts yet.

## Prompt B — Infer available_signals / missing_signals deterministically (+ tests)
TASK: Implement deterministic signal availability detection for ContextPack.

Create a pure function:
infer_signals(activity, streams=None, weather=None) -> tuple[list[str], list[str]]

Signals to consider (strings):
"splits", "heart_rate", "cadence", "elevation", "gps", "weather", "power"

Rules:
- available_signals includes a signal if the required data is present.
- missing_signals includes a signal if it is absent AND would materially affect interpretation.
- Use conservative logic: only mark available if clearly present.
- If streams is None, only infer from summary fields (e.g., avg_hr implies heart_rate is available).
- Do not call external APIs.

Add unit tests that cover:
1) activity with avg_hr only => heart_rate available, splits missing
2) activity with cadence + elevation_gain => cadence/elevation available
3) streams containing time series HR + pace => heart_rate + splits + gps available

Put the function in backend/app/services/ai/context_pack_signals.py (or similar).
Do not touch prompts yet.

## Prompt C — Build ContextPack from DB models for one activity
TASK: Build ContextPack for a single activity from existing DB models.

Add a service function:
build_context_pack(activity_id: int, db_session) -> ContextPack

Requirements:
- Load the Activity and its derived metrics + flags from existing tables/models.
- Include check-in (RPE/pain/sleep) if present, else nulls.
- Populate available_signals/missing_signals using infer_signals().
- Populate generated_at_iso with current UTC time ISO8601.
- last_7_days can be basic or placeholder strings if weekly aggregation isn’t implemented yet, but must exist.

Add a unit/integration test that inserts a minimal Activity + one DerivedMetric + one Flag into a test DB and asserts:
- ContextPack.activity.id matches
- derived_metrics contains the inserted metric with evidence
- flags contains inserted flag with evidence
- available_signals/missing_signals are populated (non-null lists)

Do not change any AI prompt usage yet. This is data assembly only.

## Prompt D — Thread ContextPack into the existing AI call (no output format change)
TASK: Pass ContextPack into the existing AI prompt as the only source of truth (no output format change yet).

Find the current AI prompt builder (backend/app/services/ai/prompts.py or similar). Update it so that:
- It calls build_context_pack(activity_id, db) and includes CONTEXT_PACK_JSON in the user message.
- The prompt explicitly says: "Use only this context pack; if data is missing, say so."
- Keep the existing output format for the current coach verdict/report (do not introduce CoachVerdictV3 yet).
- Ensure no regressions: existing endpoints should still work.

Add/adjust tests to ensure the prompt text contains:
"CONTEXT_PACK_JSON" and a JSON blob with "available_signals" and "missing_signals".

No frontend changes in this task.

## Prompt E — Add CoachVerdictV3 structured output + validation + fallback
TASK: Implement Coach Verdict v3 as strict JSON output with validation and safe fallback.

Create a new Pydantic model CoachVerdictV3 that matches this structure:

- inputs_used_line: str
- headline: { sentence: str, status: "green"|"amber"|"red" }
- why_it_matters: list[str]  # exactly 2
- scorecard: list[{ item: one of:
  "Purpose match", "Control (smoothness)", "Aerobic value", "Mechanical quality", "Risk / recoverability",
  rating: "ok"|"warn"|"fail", reason: str }]  # 3..5 items
- run_story: { start: str, middle: str, finish: str }
- lever: { category: "pacing"|"physiology"|"mechanics"|"context", signal: str, cause: str, fix: str, cue: str }
- next_steps: { tomorrow: str, next_7_days: str }
- question_for_you: str

Prompt requirements (new prompt key like "coach_verdict_v3"):
- Use ONLY the ContextPack provided.
- Must follow the status rubric:
  Green: purpose matched + controlled execution + recoverable cost
  Amber: good value but one meaningful issue
  Red: wrong stimulus OR cost/risk too high
- Safety rules: never diagnose injury; if pain high or risk flags, be conservative; never prescribe make-up workouts.

Implementation:
- Call the LLM with response_format enforcing JSON schema if supported by the client.
- Validate response with CoachVerdictV3.
- If invalid: retry once with a "repair to schema" instruction.
- If still invalid: fall back to existing rule-based advisor verdict and map it to the CoachVerdictV3 shape with conservative defaults.

Add tests:
- invalid JSON => retry invoked
- still invalid => fallback used
- pain_0_10 >= 7 OR risk flag => status cannot be green, tomorrow must be "Rest" or "easy" with caution language

Do not remove legacy report yet; keep both available.

## Prompt F — Frontend render switch behind a flag (v3 if present else legacy)
TASK: Render CoachVerdictV3 in the UI with a safe fallback.

Frontend requirements (Next.js App Router):
- Extend Activity detail page to display the CoachVerdictV3 sections in this order:
  Inputs used, Headline+status, Why it matters (2 bullets), Scorecard (5 lines max), Run story (3 acts),
  Signal→cause→fix, Next steps (2 bullets), One question.
- If v3 data exists, render it; otherwise render the existing legacy coach verdict/report.
- Add a feature flag (env or config) e.g. NEXT_PUBLIC_VERDICT_V3=1 to enable.
- Keep styling simple; no major redesign.

Add a small type guard/helper:
isCoachVerdictV3(obj): obj is CoachVerdictV3

No backend changes in this task.
