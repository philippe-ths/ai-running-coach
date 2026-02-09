# Running Coach (Strava) — Local-first MVP

Local-first app that connects to Strava, ingests running activities, computes training signals, and displays actionable analysis.

## Stack
- Backend: FastAPI + SQLAlchemy + Alembic + Postgres
- Jobs: Redis + RQ
- Frontend: Next.js (App Router)

## Repo structure
```
/
  backend/      # FastAPI app, models, schemas, services, jobs
  frontend/     # Next.js app, components, types, utilities
  docker-compose.yml
  SPEC.md
  ARCHITECTURE.md
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
rq worker --url $REDIS_URL
```

### 5) Run frontend
```bash
cd frontend
npm install
npm run dev
```

Open: http://localhost:3000

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
- Keep changes small and aligned with SPEC.md and ARCHITECTURE.md.
- When data confidence is low, default to conservative analysis.
- Models live in `backend/app/models/` (one file per model, barrel re-exported from `__init__.py`).
- Schemas live in `backend/app/schemas/` (one file per domain).
- Frontend types live in `frontend/lib/types/` (barrel re-exported from `types.ts`).
- Format utilities (`formatPace`, `formatDuration`, `formatDistanceKm`) live in `frontend/lib/format.ts`.
