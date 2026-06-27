# Testing without a Strava link

There is no Strava OAuth app for dev/local, so the usual "connect Strava -> sync"
path cannot bootstrap data outside production. This is less limiting than it
looks, because Strava is only a port: once `Activity` / `ActivityStream` rows
exist locally, the whole analyze -> coach -> frontend chain runs with zero
Strava dependency. There are two real testing paths.

## Path 1: drive the deployed app (no setup)

The production Vercel app and per-branch preview deployments are publicly
reachable and render real synced data (the frontend injects the backend's HTTP
Basic credentials server-side). For UI, render, and read-path changes, verify
against the actual deployment — navigate, interact, screenshot, inspect console
and network. This is the highest-fidelity oracle and needs no local setup.

- Production: https://ai-running-coach-eta.vercel.app/
- Previews: one per active branch (Vercel dashboard -> Active Branches).

Caveat: previews point at the same single backend/DB as production, so they show
the same data and write to the same place. Treat them as read-mostly.

## Path 2: seed the local DB from a production snapshot

For backend pipeline work (analysis, coach generation, the API itself) and for
offline iteration, copy a real snapshot from production into the local DB:

```sh
make seed-local                          # full snapshot, Strava tokens redacted
make seed-local SEED_ARGS="--activities 20"   # only the 20 most recent activities
```

What it does (`backend/scripts/seed_from_prod.py`):

1. Pulls the source URL from Railway (`DATABASE_PUBLIC_URL`) using the project
   token at `~/.railway_token`.
2. Resets the local schema (`DROP SCHEMA public CASCADE` then
   `alembic upgrade head`), so the load is deterministic even if the local DB
   was stamped at a revision from another branch.
3. Copies `users`, profiles, the Strava account, activities and their streams,
   derived metrics, coach reports, chat messages, and check-ins, in FK order.

### Safety

- **Strava tokens are redacted by default.** The copied `StravaAccount` gets
  dummy tokens, so local cannot call real Strava and cannot rotate (and thereby
  invalidate) production's live refresh token. Pass
  `SEED_ARGS="--with-live-tokens"` only when you specifically need to exercise a
  real sync, and understand it shares the production token.
- The script **refuses any non-local target** (the URL must contain `localhost`
  or `127.0.0.1`); it only ever reads from the source.

### Verifying a change against seeded data

```sh
make seed-local SEED_ARGS="--activities 20"
docker compose up -d postgres redis        # if not already up
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000
# then: curl http://localhost:8000/api/activities, or run `next dev` and browse.
```

### Browser verification without a Clerk sign-in (#488)

Phase-2 Clerk auth (ADR 0022) means the backend requires a verified session and
the frontend gates on a sign-in, which an automated agent (or a quick local
check) cannot complete without the owner's social login. To drive the seeded
stack in a browser ungated, use the supported dev-only mode:

```sh
make seed-local SEED_ARGS="--activities 20"
docker compose up -d postgres redis        # if not already up
make verify-local                          # backend :8000 + frontend :3000, ungated
```

`make verify-local` runs the backend with `LOCAL_NO_AUTH=true` (degrades to the
single seeded user, no `X-Clerk-Session-Token` needed) and the frontend with
`NEXT_PUBLIC_LOCAL_NO_AUTH=true` (Clerk gate off), then browse
http://localhost:3000. Ctrl-C stops both.

This is **dev-only and fails closed in production**: `LOCAL_NO_AUTH` is ignored
when `APP_ENV=production` (the backend stays the enforcer and unconfigured Clerk
still 503s), and `NEXT_PUBLIC_LOCAL_NO_AUTH` is honoured only in a non-production
(`next dev`) build, so it can never disable auth in a Vercel build. The normal
Clerk-on local flow (publishable key + `CLERK_JWKS_URL` set, no `*_NO_AUTH`) is
unchanged. The rq worker is not started by this target, so background jobs (sync,
coach generation) do not run; seeded data already carries their output. Start a
worker separately if you need to trigger jobs.
