# Running Coach (Strava) - Codebase Instructions

## Architecture & Boundaries
- **System**: Local-first "Client-Server-Worker" MVP using Docker Compose.
- **Backend (`backend/`)**: FastAPI, SQLAlchemy 2.0, Alembic, Postgres, Redis, RQ.
  - **Services Layer (`backend/app/services/`)**: Contains all business logic, strictly separated into:
    - `analysis/`: Rule-based metric computation (Activity Class, TRIMP, Flags).
    - `strava/`: Strava client, OAuth, token refresh.
    - `units/`: Cadence normalisation.
    - `activity_service.py`: Sync, upsert, and query logic.
  - **Models (`backend/app/models/`)**: One SQLAlchemy model per file, barrel re-exported from `__init__.py`.
  - **Schemas (`backend/app/schemas/`)**: One Pydantic schema file per domain, barrel re-exported from `__init__.py`.
- **Frontend (`frontend/`)**: Next.js 14+ (App Router), Tailwind CSS.
  - **Types (`frontend/lib/types/`)**: Per-domain TypeScript interfaces, barrel re-exported from `types.ts`.
  - **Utilities (`frontend/lib/format.ts`)**: Shared formatting functions (pace, duration, distance).
- **Worker**: Redis Queue (RQ) handles long-running tasks like Strava syncing in `backend/app/worker.py`.

## Core Concepts & Patterns
- **Rule-based Analysis**: All metrics (activity class, effort score, flags) are computed deterministically in `backend/app/services/analysis/`.
- **Modular Structure**: Models, schemas, and types are split into individual files by domain with barrel re-exports for backward compatibility.
- **Cadence Normalisation**: Strava reports half-cadence for running; the `units/cadence.py` module doubles it when appropriate.

## Backend Development (FastAPI)
- **Running**: `uvicorn app.main:app --reload --port 8000` (from `backend/` w/ venv).
- **Workers**: `rq worker --url $REDIS_URL` (requires Redis).
- **Database**:
  - Migration required for model changes: `alembic revision --autogenerate -m "desc"`.
  - Apply migrations: `alembic upgrade head`.
- **Testing**: `pytest` in `backend/`. Use `backend/tests/fixtures/` for mock Strava data.

## Frontend Development (Next.js)
- **App Router**: Uses `app/` directory structure.
  - `app/activity/[id]/page.tsx`: Dynamic route for run details.
- **Shared Types**: Keep `frontend/lib/types/` in sync with backend Pydantic models.
- **Format Utilities**: Use `frontend/lib/format.ts` for display formatting (pace, duration, distance).

## Project-Specific Rules
- **Local-First**: Do not assume external cloud services exist besides the Strava API.
- **Stream Processing**: Activity streams (HR, Watts) are stored in `ActivityStream` (Postgres) but fetched selectively to optimise performance.
- **No test data**: Never hardcode test data, seed data, dummy responses, or placeholder values anywhere in the codebase.
- **No mock calls**: Never stub, mock, or fake external API calls (e.g. Strava) in application code. Tests may mock at the HTTP boundary only (`httpx` / `respx`), but the application itself must always call real endpoints.
- **No fallbacks**: Never implement fallback logic that returns canned/default data when a real call fails. If an operation fails, surface the error — do not silently degrade.
- **No demo mode**: Do not implement demo, sandbox, or offline-simulation modes. All development and testing should use real Strava data to ensure fidelity.
- **No fake fixtures in app code**: Test fixtures belong exclusively in `backend/tests/fixtures/` and must never be imported or referenced from application code.
