# Deployment & runtime topology

How the system is wired in production and in local development, and how the pieces connect.
This is the operational reference; `project-context.md` carries the one-line summary and points here.

The original Fly/Neon/Upstash plan is decommissioned. Fly is gone: the
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
              Strava API, Anthropic API, Telegram Bot API
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
- Env vars are per-service (see "Configuration" below). The `worker` runs the RQ jobs and sends coach notifications over Telegram, so the Telegram vars must be present on `worker`, not only `web`.
- Each service's **Pre-Deploy Command** is `python -m scripts.pre_deploy` (#593): it runs the #551 env preflight, then applies `alembic upgrade head` when `RUN_MIGRATIONS=true`. Set `RUN_MIGRATIONS=true` on `web` ONLY (so schema migrations apply once per deploy and the two services never migrate concurrently); leave it unset on `worker`. This is what makes migrations auto-apply on deploy (fixes #586). See `docs/deployment/deploy-checklist.md`.

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

### Auth model (Phase 2 / Clerk)

- User identity is a Clerk session JWT verified against Clerk's JWKS (`backend/app/core/clerk_auth.py`, ADR 0022; the frontend uses `ClerkProvider` + `clerkMiddleware`). `require_current_user` resolves the user by verified email and scopes every application router per user; the user is got-or-created on first authenticated request.
- Beneath that, the backend still applies `BasicAuthMiddleware` (`backend/app/core/auth.py`) to every route, repurposed under ADR 0022 as the frontend-to-backend service secret (defense in depth), not the user gate.
- Exempt prefixes (`backend/app/main.py`): `/api/health`, `/api/webhooks`, `/api/auth/strava/callback`.
  Strava cannot send Basic credentials, so its webhook and OAuth-callback hits must be exempt; webhook
  authenticity is instead checked by `_event_is_authentic` in `app/api/webhooks.py`.
- In production a missing `BASIC_AUTH_USER`/`BASIC_AUTH_PASSWORD` fails closed with 503. Outside
  production the middleware is a no-op when either is unset, so local dev needs no credentials.
- Deploy safety (#480): in production the **web** process refuses to boot (crashes with a
  `production_config_incomplete` CRITICAL log) when `CLERK_JWKS_URL`, `BASIC_AUTH_USER`, or
  `BASIC_AUTH_PASSWORD` is unset, so a deploy whose env was not applied crash-loops and the platform
  keeps the previous healthy deploy serving instead of promoting one that 503s every route (the Phase 2
  outage). If a web deploy crash-loops right after a merge, check these env vars first. The worker is
  not gated this way (it serves no HTTP). Belt-and-suspenders: set Railway's web Health Check Path to
  `/api/health` so the cutover also waits on readiness.
- ADR 0005 originally planned magic-link sessions for this layer; that mechanism was superseded by Clerk social login (ADR 0022), which shipped in Phase 2. `BasicAuthMiddleware` was repurposed as the service secret rather than removed.
- **Known gap (#626): production authenticates against a Clerk _development_ instance (`fine-octopus-89`), not a production one.** A dev instance serves its account UI from a different site than the app and stitches the session across origins with a URL token, so a fresh signup on a new device can strand on Clerk's own `accounts.dev` page with a "Development mode" banner. It also caps around 100 users and uses Clerk's shared OAuth credentials. The owner has accepted this up to ~100 signups, so it is tracked rather than blocking. The custom-domain prerequisite was met on 2026-07-16. Runbook: **[clerk-production-cutover.md](clerk-production-cutover.md)**.
- The `azp` allowlist is armed by default with no extra env var: when `CLERK_AUTHORIZED_PARTIES` is unset, `clerk_authorized_parties_list` derives it from `CORS_ALLOWED_ORIGINS` plus `APP_BASE_URL`, so a token minted by the same Clerk instance for a different frontend origin is rejected (#707).

### Configuration (where each secret lives)

| Variable | Vercel (frontend) | Railway `web` | Railway `worker` |
| --- | --- | --- | --- |
| `BACKEND_URL` | ✓ (points at Railway web) | — | — |
| `BACKEND_BASIC_AUTH_USER` / `_PASSWORD` | ✓ | — | — |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | — | ✓ | (gate is web-only) |
| `DATABASE_URL`, `REDIS_URL` | — | ✓ | ✓ |
| `ANTHROPIC_API_KEY`, `COACH_MODEL_ID`, `COACH_PROMPT_ID` | — | ✓ | ✓ |
| `STRAVA_CLIENT_ID/SECRET`, `STRAVA_REDIRECT_URI`, `STRAVA_WEBHOOK_*` | — | ✓ (all) | ✓ (client id/secret + webhook verify token only) |
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
