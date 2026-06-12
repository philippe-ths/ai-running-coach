# Running Coach (Strava): Single-user MVP

App that connects to Strava, ingests running activities, computes training signals, and displays actionable analysis. Runs locally via `docker compose` or as a deployed single-user instance on Railway and Vercel (see [Production deployment](#production-deployment)).

Most Strava-based tools surface raw stats but offer little actionable insight — they tell you what happened, not what to do next. This project is an experiment in using computed training signals and an LLM coaching layer to bridge that gap, producing short and opinionated post-run analysis from the data you already have.

## Stack
- Backend: FastAPI + SQLAlchemy + Alembic + Postgres
- Jobs: Redis + RQ
- Frontend: Next.js (App Router)

## Production deployment

The app also runs in production on Railway (backend — web, worker, and scheduler — plus Postgres and Redis) and Vercel (frontend). It is single-user and gated by HTTP basic auth; multi-user readiness is tracked under Phase 2 (see [ADR 0005](docs/adr/0005-magic-link-is-identity-strava-is-an-integration.md) and [ADR 0006](docs/adr/0006-multi-user-drops-polling-for-user-triggered-self-healing.md)). The earlier Fly/Neon/Upstash deployment plan in [`docs/deployment/phase-1-plan.md`](docs/deployment/phase-1-plan.md) is retained as historical context; the migration to Railway is tracked in issue #97.

## Repo structure
```
/
  backend/             # FastAPI app, models, schemas, services, jobs
  frontend/            # Next.js app, components, types, utilities
  docker-compose.yml
  project-context.md   # Current product, scope, architecture, and structure reference
  README.md
```

## Prerequisites
- Docker + Docker Compose
- Python 3.11+
- Node 18+

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

### 4b) Run the polling scheduler (optional, enables ASAP coach-report emails)
In a third terminal (from `backend/`, venv activated):
```bash
python -m app.jobs.scheduler   # registers the recurring polling schedule once
rqscheduler --url $REDIS_URL   # long-running process that actually fires jobs
```
The polling fallback periodically asks Strava for new activities and runs the
ingest → analyze → coach report → email pipeline for each new one, in case
your local backend can't receive Strava webhooks directly. Email delivery is
off until `SMTP_HOST` and `NOTIFY_TO` are set in `backend/.env`.

### 5) Run frontend
```bash
cd frontend
npm install
npm run dev
```

Open: http://localhost:3000

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
- Smoke checks are now standardized as a separate repository-level command.
- Frontend unit and route-level automated coverage are also tracked separately from this baseline.

## Strava setup
1. Create a Strava API application and copy Client ID + Client Secret.
2. Set backend env vars in `backend/.env`: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REDIRECT_URI.
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
