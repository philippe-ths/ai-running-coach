# Deployment & runtime topology

How the system is wired in production and in local development, and how the pieces connect.
This is the operational reference; `project-context.md` carries the one-line summary and points here.

For the historical Fly/Neon/Upstash plan (now decommissioned) see `phase-1-plan.md`. Fly is gone: the
`running-coach-ths` app and the whole Fly account were deleted on 2026-06-02.

## Production

Two platforms. Vercel serves the frontend and proxies API traffic to the backend on Railway.

```
                 browser
                    │
         ┌──────────┴───────────┐
         │  Vercel               │   project: ai-running-coach
         │  (Next.js frontend)   │   https://ai-running-coach-eta.vercel.app
         └──────────┬───────────┘
                    │  server-side fetch with HTTP Basic auth
                    │  (BACKEND_URL + BACKEND_BASIC_AUTH_*)
                    ▼
         ┌──────────────────────┐
         │  Railway              │   project: running-coach / production
         │                       │
         │  web (FastAPI)  ◄─────┼──── Strava webhooks (no Basic auth; exempt)
         │   ├─ worker (RQ)      │
         │   └─ scheduler (rqsch)│
         │  Postgres   Redis     │
         └──────────────────────┘
                    │
                    ▼
              Strava API, Anthropic API, SMTP
```

### Vercel (frontend)

- Project `ai-running-coach`, production URL `https://ai-running-coach-eta.vercel.app`.
- Hosts the Next.js App Router app from `frontend/`.
- Does not talk to Postgres/Redis directly; all data comes from the backend over HTTP.

### Railway (backend + data)

- Project `running-coach`, environment `production`. Web service public URL `https://web-production-b64d8.up.railway.app`.
- Three app services run off the same image (`backend/Dockerfile`):
  - `web` — the FastAPI app (`uvicorn app.main:app`), serves `/api/*`.
  - `worker` — `python -m app.worker` (RQ worker with the embedded scheduler enabled), runs background jobs (ingest, analyze, coach, notify).
  - `scheduler` — `rqscheduler`, fires the recurring polling job. (ADR 0006 deletes this service under multi-user.)
- Two managed databases: `Postgres` and `Redis`.
- Env vars are per-service (see "Configuration" below). The `worker` runs the RQ jobs and sends email, so SMTP/notify vars must be present on `worker`, not only `web`.

### The Vercel → Railway connection seam

There are two paths, both adding HTTP Basic credentials server-side so the browser never sees them:

1. **Server components** call `fetchFromAPI` (`frontend/lib/api.ts`), which on the server uses
   `BACKEND_URL` directly and attaches a Basic auth header from `BACKEND_BASIC_AUTH_USER` /
   `BACKEND_BASIC_AUTH_PASSWORD`.
2. **Client components** call relative `/api/*`. The Next catch-all route handler
   `frontend/app/api/[...path]/route.ts` proxies each request to `${BACKEND_URL}/api/...`, attaching the
   same Basic auth header. It returns 500 if `BACKEND_URL` is unset, so a live prod (health returns 200)
   proves `BACKEND_URL` is configured.

`frontend/vercel.json` has a `/api/:path* → /api/:path*` rewrite that keeps `/api` paths handled by the
app (the route handler) rather than treated as static; the route handler, not the rewrite, is the proxy.

Because client calls are same-origin (the browser only ever talks to the frontend, which proxies
server-side), the browser never makes a cross-origin call to the backend on the normal path, so
`CORS_ALLOWED_ORIGINS` is not on the seam's critical path — it only matters if a direct browser→backend
call is ever added.

### Custom domain (#124)

No hostname is hardcoded in the code — every seam URL is an env var
(`BACKEND_URL`, `APP_BASE_URL`, `API_BASE_URL`, `CORS_ALLOWED_ORIGINS`,
`STRAVA_REDIRECT_URI`, `STRAVA_WEBHOOK_CALLBACK_URL`), so putting a custom domain
in front of both platforms is a config + DNS change, not a code change. The
turnkey operator checklist (DNS, per-platform env, Strava callback re-registration,
Clerk production instance, and verification) is **[custom-domain.md](custom-domain.md)**.

### Auth model (Phase 1)

- The backend gates every route with `BasicAuthMiddleware` (`backend/app/core/auth.py`).
- Exempt prefixes (`backend/app/main.py`): `/api/health`, `/api/webhooks`, `/api/auth/strava/callback`.
  Strava cannot send Basic credentials, so its webhook and OAuth-callback hits must be exempt; webhook
  authenticity is instead checked by `_event_is_authentic` in `app/api/webhooks.py`.
- In production a missing `BASIC_AUTH_USER`/`BASIC_AUTH_PASSWORD` fails closed with 503. Outside
  production the middleware is a no-op when either is unset, so local dev needs no credentials.
- This whole layer is throwaway: ADR 0005 replaces it with magic-link sessions in Phase 2.

### Configuration (where each secret lives)

| Variable | Vercel (frontend) | Railway `web` | Railway `worker` |
| --- | --- | --- | --- |
| `BACKEND_URL` | ✓ (points at Railway web) | — | — |
| `BACKEND_BASIC_AUTH_USER` / `_PASSWORD` | ✓ | — | — |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | — | ✓ | (gate is web-only) |
| `DATABASE_URL`, `REDIS_URL` | — | ✓ | ✓ |
| `ANTHROPIC_API_KEY`, `COACH_MODEL_ID`, `COACH_PROMPT_ID` | — | ✓ | ✓ |
| `STRAVA_CLIENT_ID/SECRET`, `STRAVA_REDIRECT_URI`, `STRAVA_WEBHOOK_*` | — | ✓ (all) | ✓ (client id/secret + webhook verify token only) |
| `SMTP_*`, `NOTIFY_TO` | — | ✓ | ✓ (worker sends the email) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | ✓ (inbound callback auth + button-mark edits) | ✓ (outbound sends) |
| `TELEGRAM_WEBHOOK_SECRET` | — | ✓ (web hosts the inbound callback) | — |
| `CORS_ALLOWED_ORIGINS` | — | ✓ | — |

The Vercel-side names are `BACKEND_*`; the Railway-side gate names are `BASIC_AUTH_*`. They are different
variables that must agree in value for the proxy to authenticate against the backend.

### Spend controls

The no-runaway-spend rule requires hard caps. These are platform dashboard settings, not in the repo and
not readable via the project-scoped Railway token. Verify in the Railway workspace usage limits and in
Vercel before relying on them.

## Local development

`docker compose` provides only the datastores; the app processes run on the host.

| Process | Command | Port |
| --- | --- | --- |
| Postgres | `docker compose up -d postgres redis` | host `5433` → container `5432` |
| Redis | (same compose) | `6379` |
| Backend web | `uvicorn app.main:app --reload --port 8000` | `8000` |
| Backend worker | `rq worker --with-scheduler --url $REDIS_URL` | — |
| Backend scheduler (optional) | `python -m app.jobs.scheduler` then `rqscheduler --url $REDIS_URL` | — |
| Frontend | `npm run dev` | `3000` |

- Config: `backend/.env` (from `backend/.env.example`) and `frontend/.env.local`. `DATABASE_URL` is
  required or the backend will not boot.
- Locally the frontend talks to the backend directly via `NEXT_PUBLIC_API_BASE_URL`
  (`http://localhost:8000`); the Vercel route-handler proxy and Basic auth are production-only.
- The scheduler is optional locally; run it only to exercise the polling-driven ASAP coach-report path.
- Health check: `curl http://localhost:8000/api/health`.

## Migration / drift notes

- Backend moved Fly → Railway (issue #97); Fly is fully decommissioned.
- `backend/.env.example` is missing several live settings: `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD`,
  `CORS_ALLOWED_ORIGINS`, `POLLING_LOOKBACK_SECONDS`, `BACKFILL_BATCH_SIZE`,
  `BACKFILL_BATCH_PAUSE_SECONDS`, `SENTRY_DSN`.
