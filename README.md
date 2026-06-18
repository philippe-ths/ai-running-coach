# Running Coach — an AI coach for your Strava runs (single-user MVP)

Running Coach connects to Strava, deeply processes each run into deterministic training signals, and turns those signals into an ongoing relationship with an LLM coach: short, human, opinionated coaching that arrives after you run and remembers you over time. It runs locally via `docker compose`, or as a deployed single-user instance on Railway (backend) and Vercel (frontend) — see [Production deployment](#production-deployment).

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
- **Jobs:** Redis + RQ (with `rq-scheduler` for polling)
- **Coach:** Anthropic Claude (prose message + structured tail), a deterministic policy validator, and an offline eval gate
- **Notifications:** Telegram Bot API (the deployed channel) or SMTP email (optional)
- **Frontend:** Next.js (App Router), React, Recharts, Tailwind

## Production deployment

The app runs in production on **Railway** (backend as three services off one image — `web`, `worker`, `scheduler` — plus managed Postgres and Redis) and **Vercel** (frontend). It is single-user and gated by HTTP basic auth; the deployed coach-notification channel is **Telegram** (Railway blocks outbound SMTP from the worker). Multi-user readiness is tracked under Phase 2 (see [ADR 0005](docs/adr/0005-magic-link-is-identity-strava-is-an-integration.md) and [ADR 0006](docs/adr/0006-multi-user-drops-polling-for-user-triggered-self-healing.md)). The full production and local-dev topology, the connection seam, and per-service env var ownership are documented in [`docs/deployment/topology.md`](docs/deployment/topology.md).

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

### 4b) Run the polling scheduler (optional, catches activities the webhook missed)
In a third terminal (from `backend/`, venv activated):
```bash
python -m app.jobs.scheduler   # registers the recurring polling schedule once
rqscheduler --url $REDIS_URL   # long-running process that actually fires jobs
```
The polling fallback periodically asks Strava for new activities and converges on the
same pipeline, in case your local backend cannot receive Strava webhooks directly.
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
