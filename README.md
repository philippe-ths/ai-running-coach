# Running Coach — an AI coach for your Strava runs

Running Coach connects to Strava, deeply processes each run into deterministic training signals, and turns those signals into an ongoing relationship with an LLM coach: short, human, opinionated coaching that arrives after you run and remembers you over time. It runs locally via `docker compose`, or as a deployed multi-user app on Railway (backend) and Vercel (frontend) — see [Production deployment](#production-deployment).

Most Strava-based tools surface raw stats but little actionable insight — they tell you what happened, not what to do next. This project bridges that gap: a correct, auditable deterministic substrate (metrics, blocks, training load) under an LLM coaching layer that has a voice, a point of view, and durable memory — while never letting personality override the measured data or a safety floor.

## What it does

- **Deep run analysis.** Pulls activity streams from Strava and computes effort, pace variability, HR drift, time-in-zones (binned against your own Strava HR zones), interval structure (from your recorded laps), stops, efficiency, risk flags, and a confidence read.
- **An AI coach, not a report.** After a run you get an immediate, input-free message, then a fuller coaching turn once you have had a moment (or once you reply) — delivered over Telegram with one-tap RPE/pain buttons so it never blocks on input. The coach writes human prose first and emits structure only as a thin tail.
- **Personalization you control.** Declare the coach's **Voice** (warmth / humor / directness / energy, plus presets), its **Coaching stance** (a training school of thought + two emphasis dials), and upload your own coaching **materials**. These flex how the coach talks and what it foregrounds — never the facts, your goal, or the safety floor.
- **Memory that compounds.** A durable, split memory (auditable deterministic facts + an LLM-written relationship narrative) plus a learning loop that tracks which advice you actually act on, so the coach advances the conversation instead of repeating itself.
- **Training load.** A deterministic acute / chronic / form readiness model (fitness, fatigue, form) built from your own per-activity load, shown on the activity page and read by the coach.
- **Multi-activity sessions.** Temporally-contiguous activities (a walk, then a run, then a row) are grouped into one **Block**, so the coach reasons about the whole session, not each fragment.
- **A web app.** An activity list and detail view (stream charts, splits, laps, a training-load card, the coach report and a follow-up chat), a trends dashboard, a training-load view, and a profile editor for voice / stance / materials. Light / dark / system theming.

A deterministic policy validator gates every coach message (medical scope, HR-zone language, interval claims), and an offline eval harness scores reports against a rubric so quality regressions are caught before they ship.

## How it's built (deeper docs)

- [`project-context.md`](project-context.md) — the current implementation reference (product, scope, architecture, structure).
- [`CONTEXT.md`](CONTEXT.md) — the domain glossary.
- [`docs/vision/coach-north-star.md`](docs/vision/coach-north-star.md) — the vision and roadmap behind the coaching-relationship design (now built).
- [`docs/adr/`](docs/adr/) — architecture decision records.

## Stack
- **Backend:** FastAPI, SQLAlchemy, Alembic, Postgres
- **Auth:** Clerk social login (verified email is the identity); the backend verifies the session JWT against Clerk's JWKS
- **Jobs:** Redis + RQ (the worker's embedded scheduler drains deferred and retry jobs)
- **Coach:** Anthropic Claude (prose message + structured tail), a deterministic policy validator, and an offline eval gate
- **Notifications:** Telegram Bot API (the deployed channel) or SMTP email (optional)
- **Frontend:** Next.js (App Router), React, Recharts, Tailwind

## Production deployment

The app runs in production on **Railway** (backend as two services off one image — `web`, `worker` — plus managed Postgres and Redis) and **Vercel** (frontend). It is **multi-user**, authenticated with **Clerk** social login ([ADR 0022](docs/adr/0022-identity-is-social-login-via-clerk-strava-stays-an-integration.md)): the Vercel frontend holds the Clerk session and forwards the session token, the backend verifies it against Clerk's JWKS and resolves the user from the verified email, and every query is scoped to that user. HTTP Basic auth is repurposed as the frontend↔backend service secret rather than the user gate. The deployed coach-notification channel is **Telegram** (Railway blocks outbound SMTP from the worker; per-user routing is [ADR 0023](docs/adr/0023-per-user-notifications-via-telegram-binding.md)). The polling fallback was retired in favour of user-triggered self-healing ([ADR 0006](docs/adr/0006-multi-user-drops-polling-for-user-triggered-self-healing.md)). The full production and local-dev topology, the connection seam, and per-service env var ownership are documented in [`docs/deployment/topology.md`](docs/deployment/topology.md).

## Repo structure
```
/
  backend/             # FastAPI app, models, schemas, services (analysis + coach), jobs
  frontend/            # Next.js app, components, types, utilities
  docs/                # ADRs, vision/north-star, deployment topology, testing notes
  docker-compose.yml
  project-context.md   # Current product, scope, architecture, and structure reference
  CONTEXT.md           # Domain glossary
  README.md
```

## Prerequisites
- Docker + Docker Compose
- Python 3.11+ (CI runs 3.12)
- Node 20+

## Quick start (local)

### 1) Copy env examples
```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```
Auth (Clerk) is **optional locally**: leave the `CLERK_*` and `NEXT_PUBLIC_CLERK_*` vars
empty and the app runs without sign-in, degrading to a single local user (the pre-Phase-2
behaviour). Set them to require sign-in and exercise the multi-user path.

### 2) Start Postgres + Redis
```bash
docker compose up -d postgres redis
```

### 3) Run backend
```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -U pip && pip install -e .
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/api/health`

### 4) Run worker
In a second terminal (from `backend/`, venv activated):
```bash
rq worker --with-scheduler --url $REDIS_URL
```
The worker runs the background pipeline for each new activity: ingest → analyze → assign block → generate the coach exchange → notify.

There is no separate scheduler process: the worker's `--with-scheduler` flag drains the
deferred and retry jobs, and activities the webhook missed are caught by user-triggered
self-healing — a Refresh action on app-open enqueues one bounded Strava check — rather
than polling (see [ADR 0006](docs/adr/0006-multi-user-drops-polling-for-user-triggered-self-healing.md)).

Coach notifications are off until a channel is configured: Telegram (`TELEGRAM_BOT_TOKEN`
+ `TELEGRAM_CHAT_ID`) or email (`SMTP_HOST` + `NOTIFY_TO`) in `backend/.env`.

### 5) Run frontend
```bash
cd frontend
npm install
npm run dev
```

Open: http://localhost:3000

### Coaching (optional, local)
The coach layer needs `ANTHROPIC_API_KEY` in `backend/.env`; the model and active prompt
are set via `COACH_MODEL_ID` and `COACH_PROMPT_ID`. Without a key the rest of the app
(sync, analysis, charts) still works; only the LLM coach output is skipped.

## Automated validation

The repository-level smoke suite is:

```bash
make smoke
```

This currently runs:
- backend readiness coverage via a lightweight FastAPI health check smoke test
- frontend route readiness coverage via a mocked API server plus a running Next.js app that verifies the core routes load successfully

The repository-level automated regression suite is:

```bash
make test
```

This currently runs:
- backend stable automated tests via `python -m pytest -m "not integration"`
- frontend automated regression checks via `npm run test`, which runs lint and production build validation

The offline coach-report eval harness (the quality gate for the coaching layer) runs via:

```bash
make eval            # score stored reports against the rubric (needs a local DB)
make eval-selftest   # validate the harness against its synthetic fixtures (no DB/key needed)
```

### Install backend test dependencies
If you want to run the backend suite in a fresh environment, install the test extras:

```bash
cd backend
pip install -e ".[test]"
```

### Current boundary
- `make smoke` is the fast readiness layer for basic startup and core route availability.
- `make test` is the main repo-wide regression routine.
- The backend global baseline excludes tests marked `integration`, which currently rely on local services or deeper cross-layer behavior that is not yet dependable as a routine regression check.
- Frontend unit and route-level automated coverage are tracked separately from this baseline.

## Strava setup
1. Create a Strava API application and copy Client ID + Client Secret.
2. Set backend env vars in `backend/.env`: `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REDIRECT_URI`.
3. Start the backend and click "Connect Strava" in the UI.

## Common ports
| Service  | Port |
|----------|------|
| Postgres | 5433 (mapped from container 5432) |
| Redis    | 6379 |
| Backend  | 8000 |
| Frontend | 3000 |

## Development notes
- Keep changes small and aligned with `project-context.md`.
- When data confidence is low, default to conservative analysis.
- Models live in `backend/app/models/` (one file per model, barrel re-exported from `__init__.py`).
- Schemas live in `backend/app/schemas/` (one file per domain).
- Frontend types live in `frontend/lib/types/` (barrel re-exported from `types.ts`).
- Format utilities (`formatPace`, `formatDuration`, `formatDistanceKm`) live in `frontend/lib/format.ts`.
