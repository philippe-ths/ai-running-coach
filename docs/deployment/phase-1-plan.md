# Phase 1 deployment plan: ship the single-user app to Fly.io

> **Historical — superseded.** This plan targeted Fly.io (backend), Neon (Postgres), and Upstash (Redis). The backend was later migrated to Railway, which consolidates the backend (web, worker, scheduler), Postgres, and Redis under a single workspace hard usage limit; the frontend remains on Vercel. See issue #97. This document is retained for historical context and the code/middleware changes it describes (basic auth, structured logging, settings) remain accurate; the Fly/Neon/Upstash provisioning and CI sections do not reflect the current deployment.

This is the concrete checklist for Phase 1 of the deployment roadmap agreed in a grilling session on 2026-05-28. The goal is **getting the current single-user codebase running in production on the public internet, end-to-end, with observability**, as a deliberate exercise in learning deployment without bundling other concerns.

Phase 1 explicitly does **not** include: multi-user auth (Phase 2), LLM provider abstraction (Phase 3), eval harness (Phase 3), prompt caching (Phase 3), custom domain (optional, deferrable).

The two ADRs written alongside this plan — [ADR 0005](../adr/0005-magic-link-is-identity-strava-is-an-integration.md) (identity model) and [ADR 0006](../adr/0006-multi-user-drops-polling-for-user-triggered-self-healing.md) (sync architecture under multi-user) — describe Phase 2 architecture and do not block Phase 1.

## Stack

| Layer       | Provider             | Plan                         | Monthly cost     |
|-------------|----------------------|------------------------------|------------------|
| Frontend    | Vercel               | Hobby (free)                 | $0               |
| Backend     | Fly.io               | One `shared-cpu-1x` 512MB    | ~$4–5            |
| Postgres    | Neon                 | Free tier                    | $0               |
| Redis       | Upstash              | Free tier (10k commands/day) | $0               |
| Email       | Existing SMTP        | Not changed in Phase 1       | $0               |
| Errors      | Sentry               | Developer free (5k events/mo)| $0               |
| DNS         | `.fly.dev` subdomain | Default                      | $0               |
| **Total**   |                      |                              | **~$5/mo**       |

Phase 2 may introduce: Resend (transactional email, free tier), a custom domain (~$12/year), and an upgrade to Neon's paid tier if the free quota becomes a constraint.

## Code changes in this phase

1. **Add a `Dockerfile` at repo root** (or `backend/Dockerfile`) that builds a single image containing the FastAPI app, the RQ worker, and the rq-scheduler. Use a multi-stage build: a `python:3.12-slim` builder that installs dependencies into a venv, a runtime stage that copies the venv and source. Image should expose port 8000 and define no default `CMD` — entrypoint is selected per Fly process.
2. **Add `fly.toml` at repo root** with:
   - `[processes]` defining `web = "uvicorn app.main:app --host 0.0.0.0 --port 8000"`, `worker = "rq worker"`, `scheduler = "python -m app.jobs.scheduler"`.
   - `[deploy] release_command = "alembic upgrade head"` so every deploy runs migrations against Neon before traffic shifts.
   - `[[services]]` exposing port 8000 to HTTPS with health checks against `/api/health`.
   - Region pinned to `lhr` or `cdg` for Europe / Strava webhook latency.
3. **Add HTTP basic auth middleware** (FastAPI dependency) in `app/main.py`. Reads expected username/password from settings. Applied to all routes under `/api` except `/api/webhooks/*` and `/api/health`. Throwaway in Phase 2; mark with a `TODO(phase-2): remove when session auth lands`.
4. **Update CORS allowlist** in `app/main.py`. Currently hardcoded to `http://localhost:3000` and `http://localhost:8000`. Add the Vercel preview-domain pattern and the Fly URL. Drive from settings, not hardcoded.
5. **Add Sentry SDK initialisation** in `app/main.py`. Two lines: `sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.0)` guarded by `if settings.sentry_dsn`. Free tier; no traces in Phase 1.
6. **Switch logging to structured JSON** (env-gated). Use stdlib `logging` with a JSON formatter (or `structlog`) when `ENV=production`, pretty-printed when `ENV=development`. One emit point in `app/main.py`'s startup.
7. **Add `Settings.sentry_dsn`, `Settings.basic_auth_user`, `Settings.basic_auth_password`, `Settings.env`, `Settings.app_base_url`** to `backend/app/core/config.py`. All optional in dev, required in prod.
8. **Update `frontend/lib/api.ts`** so `NEXT_PUBLIC_API_BASE_URL` works against the Fly URL in Vercel and stays `http://127.0.0.1:8000` locally. (Already env-driven; verify Vercel env vars are set.)

## Infrastructure to provision (one-time setup)

1. **Fly.io account** + `flyctl` CLI installed locally. `fly launch` to create the app, do not let it provision Postgres (we use Neon).
2. **Neon project** for production Postgres. Copy connection string (with `?sslmode=require`) into Fly secrets as `DATABASE_URL`. Run `alembic upgrade head` once locally against the Neon URL to seed the schema before the first deploy (or rely on `release_command`).
3. **Upstash Redis database** in a region close to Fly's. Copy the TLS connection URL into Fly secrets as `REDIS_URL`.
4. **Sentry project** (Python backend). Copy the DSN into Fly secrets as `SENTRY_DSN`.
5. **Two Strava API applications** at https://www.strava.com/settings/api: `Running Coach (dev)` with callback `http://localhost:8000` and webhook URL pointed at the local Cloudflare Tunnel; `Running Coach (prod)` with callback `https://<fly-app>.fly.dev` and webhook URL pointed at production. Each app's `client_id` and `client_secret` go to the appropriate environment.
6. **Cloudflare Tunnel** installed locally (`brew install cloudflared`). Stable public URL for local webhook testing against the dev Strava app.
7. **Vercel project** linked to the GitHub repo, `frontend/` set as the root. Env var `NEXT_PUBLIC_API_BASE_URL` set to the Fly URL.
8. **All secrets in Fly via `flyctl secrets set`**, never committed: `DATABASE_URL`, `REDIS_URL`, `SENTRY_DSN`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `COACH_MODEL_ID`, `COACH_PROMPT_ID`, `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD`, `APP_BASE_URL`, `ENV=production`. Existing SMTP vars (`SMTP_HOST` et al.) included if email is wanted in Phase 1; otherwise leave unset and the notifier is a no-op.

## CI/CD pipeline

Add `.github/workflows/deploy.yml`:

- On push to `main`:
  1. Run `make backend-test` (excludes integration tests per existing baseline).
  2. Run `make frontend-test` (lint + build).
  3. If both pass, run `flyctl deploy --remote-only` (uses `FLY_API_TOKEN` repo secret).
- Vercel handles frontend deploys automatically via its own GitHub integration; no Actions step needed.
- Branch protection on `main`: require the workflow to pass before merge. Disable direct pushes.

## Phase 1 done criteria

The phase is complete when all of these are demonstrably true:

1. `https://<fly-app>.fly.dev/api/health` returns `200` over HTTPS.
2. Pushing a commit to `main` runs CI, deploys backend to Fly, and deploys frontend to Vercel, all without manual intervention.
3. A real Strava activity completed on the dev runner's Garmin/phone arrives via the prod webhook and produces a `CoachReport` row in Neon.
4. The frontend on Vercel loads the activity list, the detail page, and the trends page against the Fly backend, end-to-end, behind basic auth.
5. Sentry has captured at least one deliberately-induced exception (smoke test of the integration).
6. `flyctl logs` shows structured JSON log lines from `web`, `worker`, and `scheduler` processes.
7. `alembic upgrade head` ran successfully on at least one deploy (verifiable in Fly deploy output).
8. The repo contains no committed secrets (`git log -p` clean for `.env` values; `.env` is gitignored).

## Out of scope, surfaced for Phase 2 or later

- No real auth. Basic auth is a Phase 1-only stopgap.
- Polling fallback still runs in Phase 1 (single-user; ADR 0006's removal applies only when multi-user lands).
- No custom domain; deferred until Phase 2 so user-visible URLs do not change mid-rollout.
- No log aggregation beyond `flyctl logs`. Add Better Stack / Axiom in Phase 3 if needed.
- No tracing or per-request metrics. Add OpenTelemetry in Phase 3 if needed.
- No backups configured for Neon free tier (it has its own snapshotting). Revisit on paid tier.
- No account deletion endpoint. Single-user; will be required in Phase 2.
- LLM cost is uncapped. Single-user, basic-auth-gated, low risk; will be capped in Phase 3.
