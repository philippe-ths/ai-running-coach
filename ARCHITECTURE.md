# ARCHITECTURE.md — Local-first FastAPI + SQLAlchemy + Next.js

## 1) Repo layout
/
  backend/
    app/
      api/                # FastAPI routers
      core/               # config, logging, security
      db/                 # session, base, migrations
      models/             # SQLAlchemy ORM models
      schemas/            # Pydantic models
      services/
        strava/           # Strava client, OAuth, webhook handlers
        analysis/         # metrics + classifier
        coaching/         # advice policy + generator
      workers/            # job definitions (RQ/Celery)
      main.py
    alembic/
    tests/
    pyproject.toml
    .env.example
  frontend/
    app/                  # Next.js App Router
    lib/
    components/
    .env.example
  docker-compose.yml
  SPEC.md
  ARCHITECTURE.md
  PROMPTS.md
  TODO.md
  README.md

## 2) Tech stack (MVP)
Backend:
- FastAPI
- SQLAlchemy 2.x
- Alembic
- Postgres (docker-compose)
- Background jobs: RQ + Redis (simple local-first)
- HTTP client: httpx

Frontend:
- Next.js (App Router)
- Fetch backend via REST

## 3) Environment variables
Backend (.env):
- DATABASE_URL=postgresql+psycopg://...
- REDIS_URL=redis://...
- STRAVA_CLIENT_ID=...
- STRAVA_CLIENT_SECRET=...
- STRAVA_REDIRECT_URI=http://localhost:8000/api/auth/strava/callback
- STRAVA_WEBHOOK_VERIFY_TOKEN=...
- STRAVA_WEBHOOK_CALLBACK_URL=http://localhost:8000/api/webhooks/strava
- APP_BASE_URL=http://localhost:3000
- API_BASE_URL=http://localhost:8000
- SECRET_KEY=dev-only-secret

Frontend (.env):
- NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

## 4) Strava integration design
### 4.1 OAuth
- Endpoint: GET /api/auth/strava/login
  - redirects to Strava authorize URL with scopes and redirect_uri
- Endpoint: GET /api/auth/strava/callback
  - exchanges code for access/refresh tokens
  - stores tokens in strava_accounts
  - triggers initial sync job

Token refresh:
- Before any Strava API call, if expires_at < now+60s => refresh token.

### 4.2 Webhooks
Endpoints:
- GET /api/webhooks/strava (verification challenge)
- POST /api/webhooks/strava (event receiver)

On activity create/update:
- enqueue job: sync_activity(strava_activity_id)
On activity delete:
- mark as deleted (soft delete) and remove advice/metrics.

Rate limits:
- Prefer webhook-driven sync.
- Fetch streams selectively (only for key run types or on-demand).
- Cache Strava activity payloads in DB; avoid re-fetching.

## 5) Database schema (MVP tables)
### 5.1 users
- id (uuid)
- email (nullable for MVP if no auth)
- created_at

### 5.2 strava_accounts
- id (uuid)
- user_id (fk)
- strava_athlete_id (unique)
- access_token (encrypted-at-rest if possible; for MVP plain ok)
- refresh_token
- expires_at (int timestamp)
- scope (text)
- created_at, updated_at

### 5.3 activities
- id (uuid)
- user_id (fk)
- strava_activity_id (unique)
- start_date (datetime)
- type (text)
- name (text)
- distance_m (int)
- moving_time_s (int)
- elapsed_time_s (int)
- elev_gain_m (float)
- avg_hr (float nullable)
- max_hr (float nullable)
- avg_cadence (float nullable)
- average_speed_mps (float nullable)
- raw_summary (jsonb)  # store full Strava response for audit
- is_deleted (bool default false)
- created_at, updated_at

### 5.4 activity_streams (optional in MVP; ok to stub)
- id (uuid)
- activity_id (fk)
- stream_type (text) # time, distance, heartrate, velocity_smooth...
- data (jsonb)

### 5.5 derived_metrics
- id (uuid)
- activity_id (fk unique)
- activity_class (text)
- effort_score (float)
- pace_variability (float nullable)
- hr_drift (float nullable)
- time_in_zones (jsonb nullable)
- flags (jsonb)  # list[str]
- confidence (text)
- confidence_reasons (jsonb)
- created_at, updated_at

### 5.6 advice
- id (uuid)
- activity_id (fk unique)
- verdict (text)
- evidence (jsonb)        # list[str]
- next_run (jsonb)        # structured prescription
- week_adjustment (text)
- warnings (jsonb)        # list[str]
- question (text nullable)
- full_text (text)        # rendered advice block
- created_at, updated_at

### 5.7 user_profile
- user_id (pk fk)
- goal_type (text)
- target_date (date nullable)
- experience_level (text)
- weekly_days_available (int)
- injury_notes (text)
- created_at, updated_at

### 5.8 check_ins
- id (uuid)
- activity_id (fk)
- rpe (int 1-10 nullable)
- pain_score (int 0-10 nullable)
- pain_location (text nullable)
- sleep_quality (int 1-5 nullable)
- notes (text nullable)
- created_at

## 6) Analysis engine (services/analysis)
### 6.1 Classifier (rule-based MVP)
Inputs: activity summary (+ streams when available)
Outputs: activity_class, confidence, reasons

Heuristics (MVP):
- intervals: repeated hard segments inferred by split/stream variability + pauses/recoveries
- tempo: sustained moderate-hard effort for >= 20 min with relatively stable pace/HR
- long: duration in top 20% of user’s last 4 weeks OR > 75 min if no history
- easy/recovery: low variability + low HR zone time (if HR) or user-reported RPE low
- hills: high elev_gain per km + variable grade

### 6.2 Metrics
- effort_score:
  - if HR: TRIMP-like (simple zone weighting)
  - else: moving_time_s × intensity_proxy (based on class)
- pace_variability:
  - stddev of per-km splits / average pace
- hr_drift:
  - if HR and steady segment: compare first half HR/pace vs second half

### 6.3 Flags
Generate per SPEC.md and store in derived_metrics.flags.

## 7) Coaching engine (services/coaching)
Advice is derived from:
- derived_metrics
- recent 7–14 day activity summary stats
- check-ins if present
- user_profile

Advice policy (MVP):
- If pain_severe: stop intensity recommendation; suggest easy/rest + seek pro.
- If fatigue_possible or load_spike: next run easy/recovery; reduce hard sessions.
- If intensity_too_high_for_easy: prescribe easier next run + explain.
- Else: propose next run based on missing stimulus:
  - if no strides in 10 days and mostly easy: add strides
  - if goal race exists and enough base: propose one quality session per week

## 8) Backend API endpoints (MVP)
Auth/Strava:
- GET  /api/auth/strava/login
- GET  /api/auth/strava/callback

Webhooks:
- GET  /api/webhooks/strava
- POST /api/webhooks/strava

User/profile:
- GET  /api/profile
- POST /api/profile

Activities:
- GET  /api/activities?limit=&offset=
- GET  /api/activities/{id}
- POST /api/activities/{id}/checkin
- POST /api/sync (manual sync)

Weekly:
- GET /api/week?start=YYYY-MM-DD

## 9) Frontend pages (MVP)
- / (dashboard)
  - connect button if not connected
  - current week summary
  - recent activities list
- /activity/[id]
  - activity stats
  - derived metrics + flags
  - advice block
  - check-in form

## 10) Local-first dev (docker-compose)
Services:
- postgres
- redis
- backend (uvicorn --reload)
- frontend (next dev)