# AI Running Coach (Strava) - Codebase Instructions

## Architecture & Boundaries
- **System**: Local-first "Client-Server-Worker" MVP using Docker Compose.
- **Backend (`backend/`)**: FastAPI, SQLAlchemy 2.0, Alembic, Postgres, Redis, RQ.
  - **Services Layer (`backend/app/services/`)**: Contains all business logic. strictly separated into:
    - `analysis/`: Rule-based metric computation (Activity Class, TRIMP, Flags).
    - `coaching/`: Text generation logic (Verdict, Advice).
    - `ai/`: LLM interaction and `ContextPack` construction.
    - `strava/`: External integration specific logic.
- **Frontend (`frontend/`)**: Next.js 14+ (App Router), Tailwind CSS.
- **Worker**: Redis Queue (RQ) handles long-running tasks like Strava syncing in `backend/app/worker.py`.

## Core Concepts & Patterns
- **ContextPack**: The single source of truth for AI generation.
  - Defined in `backend/app/schemas.py` and `frontend/lib/types.ts`.
  - Aggregates `Activity`, `Profile`, `DerivedMetrics`, and `CheckIn` into a deterministic JSON object to prevent hallucinations.
  - Always validate `ContextPack` integrity before calling LLMs.
- **Structured Advice (Verdict V3)**:
  - Output must follow strict JSON schema: `headline`, `scorecard`, `lever` (specific prescriptive change), and `run_story`.
  - Logic lives in `backend/app/services/coaching/v3/`.
- **Hybrid Analysis**:
  - **Metrics** are computed deterministically (rule-based) in `backend/app/services/analysis/`.
  - **Narrative** is generated via LLM using those metrics.
  - Never mix metric calculation into the LLM prompt; compute first, then prompt.

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
- **Shared Types**: Keep `frontend/lib/types.ts` in sync with backend Pydantic models (specifically `ContextPack` and `CoachVerdict`).
- **Feature Flags**: Use `frontend/lib/feature_flags.ts` to toggle between legacy advice and V3 AI advice.

## Project-Specific Rules
- **Local-First**: Do not assume external cloud services exist besides Strava API and OpenAI.
- **Stream Processing**: Activity streams (HR, Watts) are stored in `ActivityStream` (Postgres) but fetched selectively to optimize performance.

# No fallbacks 
-- **Never** implement fallback logic that bypasses the AI coaching. If the LLM fails, return an error to the user instead of degrading to a non-AI experience. This ensures we maintain a consistent product vision and avoid technical debt from supporting legacy code paths. 
-- **No "full_text" fallbacks**: The frontend should not have logic that falls back to rendering unstructured text if the structured V3 verdict is unavailable. Always require the backend to provide a valid `CoachVerdictV3` response, and handle errors gracefully on the frontend without degrading the experience.
-- **No test data**: Do not hardcode test data or mock responses. 
-- **No Demo mode**: Do not implement a "demo mode" that bypasses real Strava data or LLM calls. All development and testing should be done with real data and interactions to ensure fidelity to the user experience.