# ARCHITECTURE.md — Local-first FastAPI + SQLAlchemy + Next.js

## 1) Repo layout
```
/
  backend/
    app/
      api/                # FastAPI routers
      core/               # config, logging
      db/                 # session, base
      models/             # SQLAlchemy ORM models (one file per model)
        __init__.py       # barrel re-exports
        base.py           # generate_uuid helper
        user.py
        strava_account.py
        activity.py
        activity_stream.py
        derived_metric.py
        user_profile.py
        checkin.py
      schemas/            # Pydantic models (one file per domain)
        __init__.py       # barrel re-exports
        user.py
        activity.py
        profile.py
        checkin.py
        sync.py
        detail.py         # DerivedMetricRead, ActivityStreamRead, ActivityDetailRead
      services/
        strava/           # Strava client, OAuth, token refresh
        analysis/         # classifier, metrics, flags, engine
        units/            # cadence normalisation
        activity_service.py
      jobs/               # RQ job definitions (strava_sync)
      main.py
      worker.py
    alembic/
    tests/
    pyproject.toml
  frontend/
    app/                  # Next.js App Router pages
    lib/
      api.ts              # fetchFromAPI wrapper
      format.ts           # formatPace, formatDuration, formatDistanceKm
      types.ts            # barrel re-exports
      types/              # per-domain TS interfaces
        activity.ts
        metrics.ts
        profile.ts
    components/           # React components
  docker-compose.yml
  SPEC.md
  ARCHITECTURE.md
  README.md
```

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
- Endpoint: GET /api/auth/strava/login — redirects to Strava authorize URL
- Endpoint: GET /api/auth/strava/callback — exchanges code for tokens, stores in strava_accounts, triggers initial sync

Token refresh: before any Strava call, if expires_at < now+60s => refresh token.

### 4.2 Webhooks
- GET /api/webhooks/strava (verification challenge)
- POST /api/webhooks/strava (event receiver)

On activity create/update: enqueue sync_activity job.
On activity delete: soft-delete and remove metrics.

Rate limits: prefer webhook-driven sync; fetch streams selectively; cache payloads in DB.

## 5) Database schema
### 5.1 users
id (uuid), email (nullable), created_at

### 5.2 strava_accounts
id (uuid), user_id (fk), strava_athlete_id (unique), access_token, refresh_token, expires_at, scope, created_at, updated_at

### 5.3 activities
id (uuid), user_id (fk), strava_activity_id (unique), start_date, type, name, distance_m, moving_time_s, elapsed_time_s, elev_gain_m, avg_hr?, max_hr?, avg_cadence?, average_speed_mps?, user_intent?, raw_summary (jsonb), is_deleted, created_at, updated_at

### 5.4 activity_streams
id (uuid), activity_id (fk), stream_type, data (jsonb)

### 5.5 derived_metrics
id (uuid), activity_id (fk unique), activity_class, effort_score, pace_variability?, hr_drift?, time_in_zones (jsonb?), flags (jsonb), confidence, confidence_reasons (jsonb), created_at, updated_at

### 5.6 user_profiles
user_id (pk fk), goal_type, target_date?, experience_level, weekly_days_available, current_weekly_km?, max_hr?, upcoming_races (jsonb), injury_notes?, created_at, updated_at

### 5.7 check_ins
id (uuid), activity_id (fk), rpe?, pain_score?, pain_location?, sleep_quality?, notes?, created_at

## 6) Analysis engine (services/analysis)
### 6.1 Classifier (rule-based)
Inputs: activity summary + optional streams.
Outputs: activity_class, confidence, reasons.

Heuristics: intervals (variability + recoveries), tempo (sustained effort >= 20 min), long (top 20% duration or > 75 min), easy/recovery (low variability + low HR), hills (high elev/km).

### 6.2 Metrics
- effort_score: TRIMP-like if HR, else time x intensity proxy
- pace_variability: stddev of per-km splits / avg pace
- hr_drift: first-half vs second-half HR/pace comparison

### 6.3 Flags
Stored in derived_metrics.flags. See SPEC.md section 6 for the full list.

## 7) API endpoints
Auth: GET /api/auth/strava/login, GET /api/auth/strava/callback
Webhooks: GET + POST /api/webhooks/strava
Profile: GET /api/profile, POST /api/profile
Activities: GET /api/activities, GET /api/activities/{id}, POST /api/activities/{id}/checkin, PATCH /api/activities/{id}/intent, POST /api/sync
Health: GET /api/health

## 8) Frontend pages
- `/` — dashboard: connect button, recent activities, sync
- `/activity/[id]` — stats, metrics, flags, stream charts, intent selector, check-in
- `/profile` — profile editor (goal, experience, races, injuries)

## 9) Local-first dev (docker-compose)
- postgres (5433 -> 5432)
- redis (6379)
- backend (uvicorn --reload, 8000)
- frontend (next dev, 3000)
