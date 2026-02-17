# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Local-first running analytics app that connects to Strava, ingests running activities, computes training signals (rule-based, no ML), and displays actionable analysis. Client-Server-Worker architecture using Docker Compose.

**Stack:** FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL 16 | Redis + RQ | Next.js 14 (App Router) + TypeScript + Tailwind CSS + Recharts

## Common Commands

### Backend (run from `backend/` with venv activated)
```bash
uvicorn app.main:app --reload --port 8000     # dev server
pytest                                          # run all tests
pytest tests/test_analysis.py                   # run single test file
pytest tests/test_analysis.py::test_name -v     # run single test
alembic revision --autogenerate -m "desc"       # create migration
alembic upgrade head                            # apply migrations
rq worker --url $REDIS_URL                      # start background worker
```

### Frontend (run from `frontend/`)
```bash
npm run dev       # dev server (localhost:3000)
npm run build     # production build
npm run lint      # ESLint
```

### Infrastructure
```bash
docker compose up -d postgres redis    # start Postgres (port 5433) + Redis (6379)
```

## Architecture

### Backend (`backend/app/`)
- **`api/`** — FastAPI routers: `auth.py` (Strava OAuth), `activities.py` (CRUD + sync), `trends.py`, `profile.py`, `webhooks.py`
- **`models/`** — SQLAlchemy ORM models, one per file, barrel re-exported from `__init__.py`
- **`schemas/`** — Pydantic request/response models, one per domain, barrel re-exported from `__init__.py`
- **`services/`** — Business logic layer:
  - `strava/client.py` — OAuth, token refresh, API calls (httpx async)
  - `processing/engine.py` — Main analysis orchestrator (`process_activity`)
  - `processing/classifier.py` — Activity classification (Easy/Tempo/Interval/Long/Race/Hills)
  - `processing/metrics.py` — TRIMP, effort score, pace variability, HR drift, zones
  - `processing/flags.py` — Safety and data quality flags
  - `processing/splits.py` — Per-km splits with power, elevation
  - `activity_service.py` — Sync, upsert, query logic
  - `trends.py` — Aggregation for historical charts
- **`db/session.py`** — Engine, SessionLocal, `get_db()` dependency
- **`core/config.py`** — Pydantic settings from `.env`

### Frontend (`frontend/`)
- **`app/`** — Next.js App Router pages: dashboard (`/`), activity detail (`/activity/[id]`), trends (`/trends`), profile (`/profile`)
- **`components/`** — React components (StreamCharts, SplitsPanel, CheckInForm, etc.)
- **`lib/api.ts`** — Centralized `fetchFromAPI` wrapper (no-cache, JSON, 404→null)
- **`lib/types/`** — TypeScript interfaces mirroring backend Pydantic models, barrel re-exported from `types.ts`
- **`lib/format.ts`** — `formatPace`, `formatDuration`, `formatDistanceKm`, `formatDateLabel`
- **`next.config.js`** — Rewrites `/api/*` → `http://127.0.0.1:8000/api/*`

### Testing
- Backend tests in `backend/tests/` use in-memory SQLite via conftest fixtures
- Mock HTTP calls at the boundary only (respx/httpx); never mock in application code
- Test fixtures belong exclusively in `backend/tests/fixtures/`

## Key Rules

- **No test/dummy data** in application code — never hardcode seed data, dummy responses, or placeholders
- **No mock calls** in app code — tests may mock at HTTP boundary only; the app must always call real endpoints
- **No fallbacks** — if an operation fails, surface the error; don't silently degrade with canned defaults
- **No demo/offline mode** — all development uses real Strava data
- **Rule-based analysis only** — all metrics are deterministic, no ML
- **Cadence normalization** — Strava reports half-cadence for running; `units/cadence.py` doubles it
- **Conservative defaults** — when data confidence is low, default to conservative analysis

## Development Patterns

- **New model:** Create `backend/app/models/<name>.py`, add to barrel `__init__.py`, create Alembic migration
- **New schema:** Create `backend/app/schemas/<name>.py`, add to barrel `__init__.py`
- **New frontend type:** Create `frontend/lib/types/<name>.ts`, add to barrel `types.ts`
- **Keep frontend types in sync** with backend Pydantic schemas
- **Use `frontend/lib/format.ts`** for all display formatting
- Keep changes small and aligned with `SPEC.md` and `ARCHITECTURE.md`

## Git Workflow

Claude Code manages branching, commits, and pull requests for this project.

### Branching
Always create a feature branch from `main` before making changes:
- `feat/<short-description>` — new features
- `fix/<short-description>` — bug fixes
- `refactor/<short-description>` — refactors

### Commits
Follow conventional commit style:
- `feat:` or `feat(scope):` — new features
- `fix:` or `fix(scope):` — bug fixes
- `refactor:`, `style:`, `docs:`, `test:` — as appropriate
- Keep commits focused and atomic

### Pull Requests
After completing work, push the branch and open a PR to `main`:
```bash
git push -u origin <branch-name>
gh pr create --title "..." --body "..."
```
Include a summary and test plan in the PR body.

### After Merge
Switch back to `main` and pull latest:
```bash
git checkout main && git pull
```

## Reference Docs

- `SPEC.md` — Product goals, MVP user flows, analysis requirements, safety rules
- `ARCHITECTURE.md` — Detailed tech stack, DB schema, API endpoints, frontend pages
