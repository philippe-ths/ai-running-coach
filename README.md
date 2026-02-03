# AI Running Coach (Strava) — Local-first MVP

Local-first app that connects to Strava, ingests running activities, computes training signals, and generates short coaching advice.

## Stack
- Backend: FastAPI + SQLAlchemy + Alembic + Postgres
- Jobs: Redis + RQ
- Frontend: Next.js (App Router)

## Repo structure (expected)
/
  backend/
  frontend/
  docker-compose.yml
  SPEC.md
  ARCHITECTURE.md
  PROMPTS.md
  TODO.md

## Prereqs
- Docker + Docker Compose
- Python 3.11+
- Node 18+

## Quick start (local)
### 1) Copy env examples
Backend:
- `cp backend/.env.example backend/.env`

Frontend:
- `cp frontend/.env.example frontend/.env.local`

### 2) Start Postgres + Redis
From repo root:
- `docker compose up -d postgres redis`

### 3) Run backend
From `backend/`:
- `python -m venv .venv`
- `. .venv/bin/activate`
- `pip install -U pip`
- `pip install -e .`  (or `pip install -r requirements.txt` if you prefer)
- `alembic upgrade head`
- `uvicorn app.main:app --reload --port 8000`

Health check:
- `curl http://localhost:8000/api/health`

### 4) Run worker
In a second terminal (from `backend/`, venv activated):
- `rq worker --url $REDIS_URL`

### 5) Run frontend
From `frontend/`:
- `npm install`
- `npm run dev`

Open:
- http://localhost:3000

## Strava setup (optional for MVP demo)
1) Create a Strava API application and copy:
   - Client ID
   - Client Secret
2) Set backend env vars in `backend/.env`:
   - STRAVA_CLIENT_ID
   - STRAVA_CLIENT_SECRET
   - STRAVA_REDIRECT_URI
3) Start the backend and click “Connect Strava” in the UI.

## Demo mode (no Strava required)
For Step 10, you’ll load sample Strava-like activity JSON and run analysis + advice end-to-end.

Sample files live here:
- `backend/sample_data/strava/activities/*.json`

Notes:
- These JSON files are intentionally small and stable.
- Your Step 10 script/endpoint should read these, insert activities, compute metrics, and generate advice.

## Common ports
- Postgres: 5433 (mapped from container 5432 to avoid local conflicts)
- Redis: 6379
- Backend: 8000
- Frontend: 3000

## Development notes
- Keep changes small and aligned with SPEC.md and ARCHITECTURE.md.
- Advice must follow the fixed structure in SPEC.md.
- When data confidence is low, default to conservative recommendations.

